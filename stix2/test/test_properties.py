import datetime as dt

import pytest
import pytz

import stix2
from stix2.base import _STIXBase
from stix2.exceptions import (
    ExtraPropertiesError, STIXError, CustomContentError,
)
from stix2.properties import (
    BinaryProperty, BooleanProperty, EmbeddedObjectProperty, EnumProperty,
    FloatProperty, HexProperty, IntegerProperty, ListProperty, Property,
    StringProperty, TimestampProperty, TypeProperty,
)


def test_property():
    p = Property()

    assert p.required is False
    assert p.clean('foo') == ('foo', False)
    assert p.clean(3) == (3, False)


def test_basic_clean():
    class Prop(Property):

        def clean(self, value):
            if value == 42:
                return value
            else:
                raise ValueError("Must be 42")

    p = Prop()

    assert p.clean(42) == 42
    with pytest.raises(ValueError):
        p.clean(41)


def test_property_default():
    class Prop(Property):

        def default(self):
            return 77

    p = Prop()

    assert p.default() == 77


def test_fixed_property():
    p = Property(fixed="2.0")

    assert p.clean("2.0")
    with pytest.raises(ValueError):
        assert p.clean("x") is False
    with pytest.raises(ValueError):
        assert p.clean(2.0) is False

    assert p.default() == "2.0"
    assert p.clean(p.default())


def test_property_fixed_and_required():
    with pytest.raises(STIXError):
        Property(default=lambda: 3, required=True)


def test_list_property_property_type():
    p = ListProperty(StringProperty)

    result = p.clean(['abc', 'xyz'], False)
    assert result == (['abc', 'xyz'], False)

    with pytest.raises(ValueError):
        p.clean([], False)


def test_list_property_property_type_custom():
    class TestObj(_STIXBase):
        _type = "test"
        _properties = {
            "foo": StringProperty(),
        }
    p = ListProperty(EmbeddedObjectProperty(type=TestObj))

    objs_custom = [
        TestObj(foo="abc", bar=123, allow_custom=True),
        TestObj(foo="xyz"),
    ]

    result = p.clean(objs_custom, True)
    assert result == (objs_custom, True)

    with pytest.raises(CustomContentError):
        p.clean(objs_custom, False)

    dicts_custom = [
        {"foo": "abc", "bar": 123},
        {"foo": "xyz"},
    ]

    result = p.clean(dicts_custom, True)
    assert result == (objs_custom, True)

    with pytest.raises(ExtraPropertiesError):
        p.clean(dicts_custom, False)


def test_list_property_object_type():
    class TestObj(_STIXBase):
        _type = "test"
        _properties = {
            "foo": StringProperty(),
        }
    p = ListProperty(TestObj)

    objs = [TestObj(foo="abc"), TestObj(foo="xyz")]
    result = p.clean(objs, False)
    assert result == (objs, False)

    dicts = [{"foo": "abc"}, {"foo": "xyz"}]
    result = p.clean(dicts, False)
    assert result == (objs, False)


def test_list_property_object_type_custom():
    class TestObj(_STIXBase):
        _type = "test"
        _properties = {
            "foo": StringProperty(),
        }
    p = ListProperty(TestObj)

    objs_custom = [
        TestObj(foo="abc", bar=123, allow_custom=True),
        TestObj(foo="xyz"),
    ]

    result = p.clean(objs_custom, True)
    assert result == (objs_custom, True)

    with pytest.raises(CustomContentError):
        p.clean(objs_custom, False)

    dicts_custom = [
        {"foo": "abc", "bar": 123},
        {"foo": "xyz"},
    ]

    result = p.clean(dicts_custom, True)
    assert result == (objs_custom, True)

    with pytest.raises(ExtraPropertiesError):
        p.clean(dicts_custom, False)


def test_list_property_bad_element_type():
    with pytest.raises(TypeError):
        ListProperty(1)


def test_list_property_bad_value_type():
    class TestObj(_STIXBase):
        _type = "test"
        _properties = {
            "foo": StringProperty(),
        }

    list_prop = ListProperty(TestObj)
    with pytest.raises(ValueError):
        list_prop.clean([1], False)


def test_string_property():
    prop = StringProperty()

    assert prop.clean('foobar')
    assert prop.clean(1)
    assert prop.clean([1, 2, 3])


def test_type_property():
    prop = TypeProperty('my-type')

    assert prop.clean('my-type')
    with pytest.raises(ValueError):
        prop.clean('not-my-type')
    assert prop.clean(prop.default())


@pytest.mark.parametrize(
    "value", [
        2,
        -1,
        3.14,
        False,
    ],
)
def test_integer_property_valid(value):
    int_prop = IntegerProperty()
    assert int_prop.clean(value) is not None


@pytest.mark.parametrize(
    "value", [
        -1,
        -100,
        -50 * 6,
    ],
)
def test_integer_property_invalid_min_with_constraints(value):
    int_prop = IntegerProperty(min=0, max=180)
    with pytest.raises(ValueError) as excinfo:
        int_prop.clean(value)
    assert "minimum value is" in str(excinfo.value)


@pytest.mark.parametrize(
    "value", [
        181,
        200,
        50 * 6,
    ],
)
def test_integer_property_invalid_max_with_constraints(value):
    int_prop = IntegerProperty(min=0, max=180)
    with pytest.raises(ValueError) as excinfo:
        int_prop.clean(value)
    assert "maximum value is" in str(excinfo.value)


@pytest.mark.parametrize(
    "value", [
        "something",
        StringProperty(),
    ],
)
def test_integer_property_invalid(value):
    int_prop = IntegerProperty()
    with pytest.raises(ValueError):
        int_prop.clean(value)


@pytest.mark.parametrize(
    "value", [
        2,
        -1,
        3.14,
        False,
    ],
)
def test_float_property_valid(value):
    int_prop = FloatProperty()
    assert int_prop.clean(value) is not None


@pytest.mark.parametrize(
    "value", [
        "something",
        StringProperty(),
    ],
)
def test_float_property_invalid(value):
    int_prop = FloatProperty()
    with pytest.raises(ValueError):
        int_prop.clean(value)


@pytest.mark.parametrize(
    "value", [
        True,
        False,
        'True',
        'False',
        'true',
        'false',
        'TRUE',
        'FALSE',
        'T',
        'F',
        't',
        'f',
        1,
        0,
    ],
)
def test_boolean_property_valid(value):
    bool_prop = BooleanProperty()

    assert bool_prop.clean(value) is not None


@pytest.mark.parametrize(
    "value", [
        'abc',
        ['false'],
        {'true': 'true'},
        2,
        -1,
    ],
)
def test_boolean_property_invalid(value):
    bool_prop = BooleanProperty()
    with pytest.raises(ValueError):
        bool_prop.clean(value)


@pytest.mark.parametrize(
    "value", [
        '2017-01-01T12:34:56Z',
    ],
)
def test_timestamp_property_valid(value):
    ts_prop = TimestampProperty()
    assert ts_prop.clean(value) == (dt.datetime(2017, 1, 1, 12, 34, 56, tzinfo=pytz.utc), False)


def test_timestamp_property_invalid():
    ts_prop = TimestampProperty()
    with pytest.raises(TypeError):
        ts_prop.clean(1)
    with pytest.raises(ValueError):
        ts_prop.clean("someday sometime")


def test_binary_property():
    bin_prop = BinaryProperty()

    assert bin_prop.clean("TG9yZW0gSXBzdW0=")
    with pytest.raises(ValueError):
        bin_prop.clean("foobar")


def test_hex_property():
    hex_prop = HexProperty()

    assert hex_prop.clean("4c6f72656d20497073756d")
    with pytest.raises(ValueError):
        hex_prop.clean("foobar")


@pytest.mark.parametrize(
    "value", [
        ['a', 'b', 'c'],
        ('a', 'b', 'c'),
        'b',
    ],
)
def test_enum_property_valid(value):
    enum_prop = EnumProperty(value)
    assert enum_prop.clean('b', False)


def test_enum_property_clean():
    enum_prop = EnumProperty(['1'])
    assert enum_prop.clean(1, False) == ('1', False)


def test_enum_property_invalid():
    enum_prop = EnumProperty(['a', 'b', 'c'])
    with pytest.raises(ValueError):
        enum_prop.clean('z', False)


def test_enum_property_custom():
    enum_prop = EnumProperty(['a', 'b', 'c'])
    result = enum_prop.clean("z", True)
    assert result == ("z", True)
