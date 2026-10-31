---
title: Reproducible and secure builds of Python images
author: MOZGII, Inc.
lang: en
---

# Reproducible and secure builds of Python images

How to make a Python image's inputs fixed and its build repeatable, and how to keep secrets out of it and show what is inside: pinning by digest, `SOURCE_DATE_EPOCH` with hash-based bytecode, what still differs between two uv builds, build secrets for private indexes, SBOMs and provenance attestations, vulnerability scanning, and signing. The reproducibility results below were measured with Docker 29.4, buildx 0.33 and uv 0.13.6 in October 2026.

## Contents

This reference has 8 sections.

1. Pinning every input
2. SOURCE_DATE_EPOCH and timestamps
3. Bytecode and what stays different with uv
4. Checking reproducibility
5. Build secrets for private package indexes
6. SBOMs and provenance attestations
7. Vulnerability scanning and signing
8. Gotchas

## 1. Pinning every input

A build is repeatable only if its inputs are fixed: the base image, the uv binary, the Python packages and any system packages. Tags move; digests do not.

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.14-slim-trixie@sha256:<digest> AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.6@sha256:<digest> /uv /uvx /bin/
```

- Read a digest with `docker buildx imagetools inspect python:3.14-slim-trixie` (the top-level index digest covers all architectures).
- Let a bot (Dependabot, Renovate) propose digest updates; a pinned base without updates collects security fixes it never gets.
- Python packages are pinned by `uv.lock` with hashes; `uv sync --locked` refuses a lock that does not match `pyproject.toml`. `exclude-newer` in `[tool.uv]` also fixes resolution in time (the `uv-projects` skill).
- System packages from `apt-get install` are not pinned by default; pin versions (`libpq5=17.*`) or use a dated snapshot mirror when bit-for-bit results matter.
- uv publishes signed provenance for its images: `gh attestation verify --owner astral-sh oci://ghcr.io/astral-sh/uv:0.13.6`.

## 2. SOURCE_DATE_EPOCH and timestamps

`SOURCE_DATE_EPOCH` is the standard variable for "pretend the build happened at this time". BuildKit uses it for image metadata, and with the image exporter's `rewrite-timestamp=true` option it also sets every file's modification time in the layers to that moment.

```bash
export SOURCE_DATE_EPOCH="$(git log -1 --pretty=%ct)"          # the last commit's time
docker buildx build --build-arg SOURCE_DATE_EPOCH \
  --output type=oci,dest=image.tar,rewrite-timestamp=true .
```

- On Docker 29.4 with the containerd image store, `--output type=docker,rewrite-timestamp=true` failed with `exporter option "rewrite-timestamp" conflicts with "unpack"`; export to an OCI tar or push to a registry (`type=image,push=true,rewrite-timestamp=true`).
- The build arg is visible to `RUN` steps only in stages that declare it with `ARG SOURCE_DATE_EPOCH`.

## 3. Bytecode and what stays different with uv

Compiled `.pyc` files store the source file's modification time by default, and uv writes installed files with the time of installation, so two builds produce different bytecode. When `SOURCE_DATE_EPOCH` is set in the environment, Python's `py_compile` writes hash-based `.pyc` files instead, which are identical across builds.

```dockerfile
FROM python:3.14-slim-trixie AS build
# ARG exposes the build arg to RUN steps, so Python writes hash-based .pyc files
ARG SOURCE_DATE_EPOCH
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1
# ... uv sync steps as in the skill ...
```

Measured result of two clean builds with `--no-cache`, `ARG SOURCE_DATE_EPOCH` and `rewrite-timestamp=true`:

| Part of the image | Same in both builds |
|---|---|
| base image layers | yes |
| third-party packages in `.venv`, including `.pyc` | yes (without the `ARG`: no, the `.pyc` headers differed) |
| the project's own `.dist-info` | no: uv writes `uv_cache.json` with the build time for a local project, and `RECORD` lists its hash |
| image digest | no, because of the line above |

So with uv today the realistic target is reproducible dependency layers and pinned inputs, not a byte-identical final image. If the project must be byte-identical, install it from a wheel built in CI and verified by hash, and compare the images with a diff tool rather than by digest.

## 4. Checking reproducibility

Check reproducibility by building twice without cache and comparing layer digests: equal digests mean equal bytes, and the first differing layer points at the step to fix.

```bash
for i in 1 2; do
  docker buildx build --no-cache --build-arg SOURCE_DATE_EPOCH=1793145600 \
    --output type=oci,dest=build$i.tar,rewrite-timestamp=true .
  mkdir -p build$i && tar -xf build$i.tar -C build$i
done
python3 - <<'EOF'
import json, pathlib
for d in ("build1", "build2"):
    index = json.loads(pathlib.Path(d, "index.json").read_text())
    manifest = index["manifests"][0]["digest"].split(":")[1]
    layers = json.loads(pathlib.Path(d, "blobs", "sha256", manifest).read_text())["layers"]
    print(d, [layer["digest"][7:19] for layer in layers])
EOF
```

To see which files differ, extract the two differing layer blobs (`tar -xf build1/blobs/sha256/<digest>`) into folders and run `diff -rq`.

## 5. Build secrets for private package indexes

A token for a private index must never be in a `COPY`ed file, an `ARG` or an `ENV`: all of them end up in the image or its history. BuildKit secret mounts expose a secret to one `RUN` step only.

```dockerfile
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=uv_index_password,env=UV_INDEX_PRIVATE_PASSWORD \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    UV_INDEX_PRIVATE_USERNAME=__token__ uv sync --locked --no-install-project
```

```bash
export UV_INDEX_PASSWORD_VALUE="$(security find-generic-password -s private-index -w)"   # macOS Keychain, for example
docker buildx build --secret id=uv_index_password,env=UV_INDEX_PASSWORD_VALUE .
```

- `UV_INDEX_<NAME>_USERNAME` and `UV_INDEX_<NAME>_PASSWORD` match an index named `private` in `[[tool.uv.index]]`.
- On CI, pass the secret from the CI secret store the same way; never echo it.
- `docker history --no-trunc` on the result must show no token.

## 6. SBOMs and provenance attestations

An SBOM (software bill of materials) lists every package in the image; a provenance attestation records how, where and from what source it was built. BuildKit attaches both to the image when it is pushed.

```bash
docker buildx build --sbom=true --provenance=mode=max \
  -t registry.example.com/invoice-sync:1.4.0 --push .
docker buildx imagetools inspect registry.example.com/invoice-sync:1.4.0 --format '{{ json .SBOM }}' | head -c 400
uv export --format cyclonedx1.5 --no-dev -o sbom.cdx.json    # the Python dependencies alone, from uv.lock
```

The BuildKit SBOM scanner finds Python packages from their `.dist-info` folders in the image; `uv export --format cyclonedx1.5` gives the same list straight from the lock file, which is useful before an image exists.

## 7. Vulnerability scanning and signing

A scanner matches the image's packages (Debian and Python) against vulnerability databases; run it in CI on every build and again on a schedule, since new vulnerabilities appear in old images.

- `docker scout cves <image>`, Trivy (`trivy image <image>`) and Grype (`grype <image>`) all read Debian packages and Python `.dist-info` metadata.
- `pip-audit -r requirements.txt` on `uv export --format requirements.txt --no-dev` output checks the locked Python packages before the image is built.
- Fix by rebuilding on a newer base digest (most Debian findings) or upgrading the Python package in `uv.lock`; document accepted risks for findings without fixes.
- Sign images with cosign (`cosign sign registry.example.com/invoice-sync@sha256:<digest>`), keyless through the CI's OIDC identity, and verify signatures at deploy time with an admission policy.

## 8. Gotchas

These are the reproducibility and supply-chain problems seen most.

1. **Two builds from the same commit have different digests.** Timestamps in files and bytecode. Set `SOURCE_DATE_EPOCH`, declare it with `ARG` in the build stage, and export with `rewrite-timestamp=true`; expect the uv project layer to differ still.
2. **`exporter option "rewrite-timestamp" conflicts with "unpack"`.** The local `docker` exporter unpacks images. Export to OCI or push to a registry.
3. **A token appears in `docker history`.** It was passed as `ARG` or `ENV`. Use `--mount=type=secret` and rotate the leaked token.
4. **The base image never gets security fixes.** Pinned by digest with no update automation. Let Renovate or Dependabot bump digests weekly.
5. **The scanner reports hundreds of findings in the full Debian image.** Build tools in the final stage. Use a slim or distroless final stage.
