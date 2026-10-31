# Python descriptors in depth

A descriptor is any object that defines `__get__`, `__set__` or `__delete__` and is stored as a class attribute. Attribute access on an instance consults it instead of the instance's own `__dict__`, so one descriptor object can validate, convert, compute or cache an attribute for every instance of the class. Properties, bound methods, `classmethod`, `staticmethod`, `functools.cached_property` and `__slots__` are all built on this protocol.

## Data and non-data descriptors

A data descriptor defines `__set__` or `__delete__`; a non-data descriptor defines only `__get__`. The difference decides who wins when the instance's `__dict__` holds the same name.

| Kind | Defines | Instance `__dict__` with the same name | Examples |
|---|---|---|---|
| data descriptor | `__get__` plus `__set__` or `__delete__` | ignored: the descriptor wins | `property`, slot members, validated fields |
| non-data descriptor | `__get__` only | wins over the descriptor | functions (methods), `cached_property`, `classmethod`, `staticmethod` |

This snippet shows both rules side by side.

```python
class NonData:
    def __get__(self, obj, objtype=None):
        return "from descriptor"


class Data(NonData):
    def __set__(self, obj, value):
        raise AttributeError("read-only")


class Host:
    nd = NonData()
    d = Data()


h = Host()
h.__dict__["nd"] = "from instance"
h.__dict__["d"] = "from instance"
h.nd        # 'from instance': the instance dict shadows a non-data descriptor
h.d         # 'from descriptor': a data descriptor always wins
```

The non-data rule is why you can shadow a method on one instance (`obj.method = fake` in a test), and why `cached_property` works: the first call stores the value in the instance `__dict__`, and later reads find it there without calling the descriptor again.

## Attribute lookup order

Attribute lookup on an instance, `obj.name`, is implemented by `object.__getattribute__` and follows a fixed order.

1. Find `name` on the type, walking the MRO (`type(obj).__mro__`).
2. If that class attribute is a data descriptor, return `attr.__get__(obj, type(obj))`.
3. Otherwise, if `name` is in `obj.__dict__`, return that value.
4. Otherwise, if the class attribute is a non-data descriptor, return `attr.__get__(obj, type(obj))`.
5. Otherwise, if the class attribute exists, return it as is.
6. Otherwise raise `AttributeError`, which makes Python call `__getattr__` if the class defines one.

Assignment, `obj.name = value`, checks the type for a data descriptor with `__set__` and calls it; otherwise it writes to `obj.__dict__`. On the class itself (`Order.price`), `__get__` is called with `obj=None`, which is why descriptors return `self` in that case: tools such as `help()`, documentation generators and dataclasses inspect the descriptor object.

Special methods (`__len__`, `__eq__`, `__iter__`) skip this whole process: the interpreter fetches them straight from the type. That is why assigning `obj.__len__ = ...` on an instance has no effect on `len(obj)`.

## How functions become bound methods

A function stored on a class is a non-data descriptor. Reading it through an instance calls `function.__get__(instance, cls)`, which returns a bound method that remembers the instance in `__self__` and the function in `__func__`.

```python
class K:
    def m(self):
        return self


k = K()
bound = K.__dict__["m"].__get__(k, K)   # exactly what k.m does
bound() is k                            # True
bound.__self__ is k, bound.__func__ is K.__dict__["m"]
```

- `classmethod.__get__` binds to the class instead of the instance; `staticmethod.__get__` returns the plain function.
- Each `k.m` creates a new bound method object, so `k.m is k.m` is `False` while `k.m == k.m` is `True`. Store the bound method once if you register it as a callback and want to unregister it later.
- A callable object that is not a function (an instance with `__call__`, a `functools.partial`) does not bind: stored on a class, it receives no `self`. Wrap it in a function or use `functools.partialmethod`.

## A typed validating descriptor

A validating descriptor typed with `@overload` on `__get__` tells type checkers that `Order.price` is the descriptor while `order.price` is a `float`.

```python
from typing import overload


class Positive:
    """Rejects zero and negative values; stores the value in the instance __dict__."""

    def __set_name__(self, owner: type, name: str) -> None:
        self.public = name
        self.private = "_" + name

    @overload
    def __get__(self, obj: None, objtype: type | None = None) -> "Positive": ...
    @overload
    def __get__(self, obj: object, objtype: type | None = None) -> float: ...
    def __get__(self, obj: object | None, objtype: type | None = None) -> "Positive | float":
        if obj is None:
            return self
        return getattr(obj, self.private)

    def __set__(self, obj: object, value: float) -> None:
        if value <= 0:
            raise ValueError(f"{self.public} must be > 0, got {value!r}")
        setattr(obj, self.private, value)


class Order:
    price = Positive()
    quantity = Positive()

    def __init__(self, price: float, quantity: float) -> None:
        self.price = price          # runs Positive.__set__
        self.quantity = quantity


vars(Order(9.5, 2))                 # {'_price': 9.5, '_quantity': 2}
```

- `__set_name__` runs only for attributes present in the class body when the class is created. A descriptor attached later (`Order.extra = Positive()`) never learns its name; call `__set_name__` by hand if you must do that.
- Store the value under a different name (`_price`) or in `obj.__dict__[name]`. Storing it on the descriptor itself (`self.value = value`) shares one value across every instance, a classic bug.
- When validation is all you need and the class is a data holder, pydantic or attrs validators may already cover it (the `python-data-models` skill).

## Per-instance storage for slotted classes

Slotted classes have no instance `__dict__`, so a descriptor needs somewhere else to keep the value. A `weakref.WeakKeyDictionary` keyed by the instance works without keeping instances alive; the class must list `"__weakref__"` in its slots.

```python
import weakref


class Tracked:
    def __init__(self) -> None:
        self.values: weakref.WeakKeyDictionary[object, int] = weakref.WeakKeyDictionary()

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        return self.values.get(obj, 0)

    def __set__(self, obj, value: int) -> None:
        self.values[obj] = value


class Widget:
    __slots__ = ("__weakref__",)
    count = Tracked()
```

The instance must be hashable for this to work. If it is not, key by `id(obj)` and clean up with `weakref.finalize(obj, self.values.pop, id(obj), None)`. A simpler alternative is a named slot the descriptor reads and writes: list `"_count"` in `__slots__` and store there.

Slots themselves are descriptors: `__slots__ = ("x",)` puts a `member_descriptor` called `x` on the class, which reads and writes a fixed offset in the instance. That is why a class attribute with the same name as a slot raises `ValueError` at class creation.

## Writing a cached property

`functools.cached_property` is a non-data descriptor that computes the value once and writes it into the instance `__dict__`. A minimal version shows the whole mechanism.

```python
class cached:
    def __init__(self, fn):
        self.fn = fn

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        value = self.fn(obj)
        obj.__dict__[self.name] = value     # later reads find this first and never reach __get__
        return value
```

- `del obj.total` removes the cached value; the next read computes it again. That is the invalidation hook.
- Since 3.12 the standard `cached_property` holds no lock, so two threads reading at once may both compute the value. Guard expensive or side-effecting computations with your own lock.
- A cached property on a frozen dataclass still works because it writes to `__dict__` directly, but with `slots=True` there is no `__dict__` and it fails.

## Descriptors on the metaclass

A descriptor placed on a metaclass applies to attribute access on the class itself. A `property` defined on a metaclass is the supported way to have a computed class attribute, now that stacking `classmethod` on `property` was removed in 3.13.

```python
class Meta(type):
    @property
    def table_name(cls) -> str:
        return cls.__name__.lower() + "s"


class User(metaclass=Meta): ...


User.table_name     # 'users'; not visible on instances: User().table_name raises AttributeError
```

Use this sparingly: a metaclass blocks mixing the class with others that have their own metaclass. A plain class attribute set in `__init_subclass__` is often enough.

## Checklist for a descriptor

- [ ] `__get__` returns `self` when `obj is None`.
- [ ] The value lives on the instance (its `__dict__`, a slot or a weak-keyed map), never on the descriptor.
- [ ] `__set_name__` records the name; the descriptor is created in the class body.
- [ ] Data descriptor when assignments must be checked; non-data when the instance may override or cache.
- [ ] `@overload` on `__get__` so type checkers see the instance type.
