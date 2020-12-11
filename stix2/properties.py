"""Classes for representing properties of STIX Objects and Cyber Observables."""

import base64
import binascii
import copy
import inspect
import re
import uuid

from six import string_types, text_type

import stix2
import stix2.hashes

from .base import _STIXBase
from .exceptions import CustomContentError, DictionaryKeyError, STIXError
from .parsing import STIX2_OBJ_MAPS, parse, parse_observable
from .utils import _get_dict, get_class_hierarchy_names, parse_into_datetime

try:
    from collections.abc import Mapping
except ImportError:
    from collections import Mapping

TYPE_REGEX = re.compile(r'^\-?[a-z0-9]+(-[a-z0-9]+)*\-?$')
TYPE_21_REGEX = re.compile(r'^([a-z][a-z0-9]*)+(-[a-z0-9]+)*\-?$')
ERROR_INVALID_ID = (
    "not a valid STIX identifier, must match <object-type>--<UUID>: {}"
)


def _check_uuid(uuid_str, spec_version):
    """
    Check whether the given UUID string is valid with respect to the given STIX
    spec version.  STIX 2.0 requires UUIDv4; 2.1 only requires the RFC 4122
    variant.

    :param uuid_str: A UUID as a string
    :param spec_version: The STIX spec version
    :return: True if the UUID is valid, False if not
    :raises ValueError: If uuid_str is malformed
    """
    uuid_obj = uuid.UUID(uuid_str)

    ok = uuid_obj.variant == uuid.RFC_4122
    if ok and spec_version == "2.0":
        ok = uuid_obj.version == 4

    return ok


def _validate_id(id_, spec_version, required_prefix):
    """
    Check the STIX identifier for correctness, raise an exception if there are
    errors.

    :param id_: The STIX identifier
    :param spec_version: The STIX specification version to use
    :param required_prefix: The required prefix on the identifier, if any.
        This function doesn't add a "--" suffix to the prefix, so callers must
        add it if it is important.  Pass None to skip the prefix check.
    :raises ValueError: If there are any errors with the identifier
    """
    if required_prefix:
        if not id_.startswith(required_prefix):
            raise ValueError("must start with '{}'.".format(required_prefix))

    try:
        if required_prefix:
            uuid_part = id_[len(required_prefix):]
        else:
            idx = id_.index("--")
            uuid_part = id_[idx+2:]

        result = _check_uuid(uuid_part, spec_version)
    except ValueError:
        # replace their ValueError with ours
        raise ValueError(ERROR_INVALID_ID.format(id_))

    if not result:
        raise ValueError(ERROR_INVALID_ID.format(id_))


def _validate_type(type_, spec_version):
    """
    Check the STIX type name for correctness, raise an exception if there are
    errors.

    :param type_: The STIX type name
    :param spec_version: The STIX specification version to use
    :raises ValueError: If there are any errors with the identifier
    """
    if spec_version == "2.0":
        if not re.match(TYPE_REGEX, type_):
            raise ValueError(
                "Invalid type name '%s': must only contain the "
                "characters a-z (lowercase ASCII), 0-9, and hyphen (-)." %
                type_,
            )
    else:  # 2.1+
        if not re.match(TYPE_21_REGEX, type_):
            raise ValueError(
                "Invalid type name '%s': must only contain the "
                "characters a-z (lowercase ASCII), 0-9, and hyphen (-) "
                "and must begin with an a-z character" % type_,
            )

    if len(type_) < 3 or len(type_) > 250:
        raise ValueError(
            "Invalid type name '%s': must be between 3 and 250 characters." % type_,
        )


class Property(object):
    """Represent a property of STIX data type.

    Subclasses can define the following attributes as keyword arguments to
    ``__init__()``.

    Args:
        required (bool): If ``True``, the property must be provided when
            creating an object with that property. No default value exists for
            these properties. (Default: ``False``)
        fixed: This provides a constant default value. Users are free to
            provide this value explicity when constructing an object (which
            allows you to copy **all** values from an existing object to a new
            object), but if the user provides a value other than the ``fixed``
            value, it will raise an error. This is semantically equivalent to
            defining both:

            - a ``clean()`` function that checks if the value matches the fixed
              value, and
            - a ``default()`` function that returns the fixed value.

    Subclasses can also define the following functions:

    - ``def clean(self, value, allow_custom) -> (any, has_custom):``
        - Return a value that is valid for this property, and enforce and
          detect value customization.  If ``value`` is not valid for this
          property, you may attempt to transform it first.  If ``value`` is not
          valid and no such transformation is possible, it must raise an
          exception.  The method is also responsible for enforcing and
          detecting customizations.  If allow_custom is False, no customizations
          must be allowed.  If any are encountered, an exception must be raised
          (e.g. CustomContentError).  If none are encountered, False must be
          returned for has_custom.  If allow_custom is True, then the clean()
          method is responsible for detecting any customizations in the value
          (just because the user has elected to allow customizations doesn't
          mean there actually are any).  The method must return an appropriate
          value for has_custom.  Customization may not be applicable/possible
          for a property.  In that case, allow_custom can be ignored, and
          has_custom must be returned as False.

    - ``def default(self):``
        - provide a default value for this property.
        - ``default()`` can return the special value ``NOW`` to use the current
            time. This is useful when several timestamps in the same object
            need to use the same default value, so calling now() for each
            property-- likely several microseconds apart-- does not work.

    Subclasses can instead provide a lambda function for ``default`` as a
    keyword argument. ``clean`` should not be provided as a lambda since
    lambdas cannot raise their own exceptions.

    When instantiating Properties, ``required`` and ``default`` should not be
    used together. ``default`` implies that the property is required in the
    specification so this function will be used to supply a value if none is
    provided. ``required`` means that the user must provide this; it is
    required in the specification and we can't or don't want to create a
    default value.

    """

    def _default_clean(self, value, allow_custom=False):
        if value != self._fixed_value:
            raise ValueError("must equal '{}'.".format(self._fixed_value))
        return value, False

    def __init__(self, required=False, fixed=None, default=None):
        self.required = required

        if required and default:
            raise STIXError(
                "Cant't use 'required' and 'default' together. 'required'"
                "really means 'the user must provide this.'",
            )

        if fixed:
            self._fixed_value = fixed
            self.clean = self._default_clean
            self.default = lambda: fixed
        if default:
            self.default = default

    def clean(self, value, allow_custom=False):
        return value, False


class ListProperty(Property):

    def __init__(self, contained, **kwargs):
        """
        ``contained`` should be a Property class or instance, or a _STIXBase
        subclass.
        """
        self.contained = None

        if inspect.isclass(contained):
            # Property classes are instantiated; _STIXBase subclasses are left
            # as-is.
            if issubclass(contained, Property):
                self.contained = contained()
            elif issubclass(contained, _STIXBase):
                self.contained = contained

        elif isinstance(contained, Property):
            self.contained = contained

        if not self.contained:
            raise TypeError(
                "Invalid list element type: {}".format(
                    str(contained),
                ),
            )

        super(ListProperty, self).__init__(**kwargs)

    def clean(self, value, allow_custom):
        try:
            iter(value)
        except TypeError:
            raise ValueError("must be an iterable.")

        if isinstance(value, (_STIXBase, string_types)):
            value = [value]

        result = []
        has_custom = False
        if isinstance(self.contained, Property):
            for item in value:
                valid, temp_custom = self.contained.clean(item, allow_custom)
                result.append(valid)
                has_custom = has_custom or temp_custom

        else:  # self.contained must be a _STIXBase subclass
            for item in value:
                if isinstance(item, self.contained):
                    valid = item

                elif isinstance(item, Mapping):
                    # attempt a mapping-like usage...
                    valid = self.contained(allow_custom=allow_custom, **item)

                else:
                    raise ValueError("Can't create a {} out of {}".format(
                        self.contained._type, str(item),
                    ))

                result.append(valid)
                has_custom = has_custom or valid.has_custom

        if not allow_custom and has_custom:
            raise CustomContentError("custom content encountered")

        # STIX spec forbids empty lists
        if len(result) < 1:
            raise ValueError("must not be empty.")

        return result, has_custom


class StringProperty(Property):

    def __init__(self, **kwargs):
        super(StringProperty, self).__init__(**kwargs)

    def clean(self, value, allow_custom=False):
        if not isinstance(value, string_types):
            value = text_type(value)
        return value, False


class TypeProperty(Property):

    def __init__(self, type, spec_version=stix2.DEFAULT_VERSION):
        _validate_type(type, spec_version)
        self.spec_version = spec_version
        super(TypeProperty, self).__init__(fixed=type)


class IDProperty(Property):

    def __init__(self, type, spec_version=stix2.DEFAULT_VERSION):
        self.required_prefix = type + "--"
        self.spec_version = spec_version
        super(IDProperty, self).__init__()

    def clean(self, value, allow_custom=False):
        _validate_id(value, self.spec_version, self.required_prefix)
        return value, False

    def default(self):
        return self.required_prefix + str(uuid.uuid4())


class IntegerProperty(Property):

    def __init__(self, min=None, max=None, **kwargs):
        self.min = min
        self.max = max
        super(IntegerProperty, self).__init__(**kwargs)

    def clean(self, value, allow_custom=False):
        try:
            value = int(value)
        except Exception:
            raise ValueError("must be an integer.")

        if self.min is not None and value < self.min:
            msg = "minimum value is {}. received {}".format(self.min, value)
            raise ValueError(msg)

        if self.max is not None and value > self.max:
            msg = "maximum value is {}. received {}".format(self.max, value)
            raise ValueError(msg)

        return value, False


class FloatProperty(Property):

    def __init__(self, min=None, max=None, **kwargs):
        self.min = min
        self.max = max
        super(FloatProperty, self).__init__(**kwargs)

    def clean(self, value, allow_custom=False):
        try:
            value = float(value)
        except Exception:
            raise ValueError("must be a float.")

        if self.min is not None and value < self.min:
            msg = "minimum value is {}. received {}".format(self.min, value)
            raise ValueError(msg)

        if self.max is not None and value > self.max:
            msg = "maximum value is {}. received {}".format(self.max, value)
            raise ValueError(msg)

        return value, False


class BooleanProperty(Property):
    _trues = ['true', 't', '1', 1, True]
    _falses = ['false', 'f', '0', 0, False]

    def clean(self, value, allow_custom=False):

        if isinstance(value, string_types):
            value = value.lower()

        if value in self._trues:
            result = True
        elif value in self._falses:
            result = False
        else:
            raise ValueError("must be a boolean value.")

        return result, False


class TimestampProperty(Property):

    def __init__(self, precision="any", precision_constraint="exact", **kwargs):
        self.precision = precision
        self.precision_constraint = precision_constraint

        super(TimestampProperty, self).__init__(**kwargs)

    def clean(self, value, allow_custom=False):
        return parse_into_datetime(
            value, self.precision, self.precision_constraint,
        ), False


class DictionaryProperty(Property):

    def __init__(self, spec_version=stix2.DEFAULT_VERSION, **kwargs):
        self.spec_version = spec_version
        super(DictionaryProperty, self).__init__(**kwargs)

    def clean(self, value, allow_custom=False):
        try:
            dictified = _get_dict(value)
        except ValueError:
            raise ValueError("The dictionary property must contain a dictionary")
        for k in dictified.keys():
            if self.spec_version == '2.0':
                if len(k) < 3:
                    raise DictionaryKeyError(k, "shorter than 3 characters")
                elif len(k) > 256:
                    raise DictionaryKeyError(k, "longer than 256 characters")
            elif self.spec_version == '2.1':
                if len(k) > 250:
                    raise DictionaryKeyError(k, "longer than 250 characters")
            if not re.match(r"^[a-zA-Z0-9_-]+$", k):
                msg = (
                    "contains characters other than lowercase a-z, "
                    "uppercase A-Z, numerals 0-9, hyphen (-), or "
                    "underscore (_)"
                )
                raise DictionaryKeyError(k, msg)

        if len(dictified) < 1:
            raise ValueError("must not be empty.")

        return dictified, False


class HashesProperty(DictionaryProperty):

    def __init__(self, spec_hash_names, **kwargs):
        super().__init__(**kwargs)

        self.__spec_hash_names = spec_hash_names

        # Map hash algorithm enum to the given spec mandated name, for those
        # names which are recognized as hash algorithms by this library.
        self.__alg_to_spec_name = {}
        for spec_hash_name in spec_hash_names:
            alg = stix2.hashes.infer_hash_algorithm(spec_hash_name)
            if alg:
                self.__alg_to_spec_name[alg] = spec_hash_name

    def clean(self, value, allow_custom):
        # ignore the has_custom return value here; there is no customization
        # of DictionaryProperties.
        clean_dict, _ = super().clean(value, allow_custom)

        spec_dict = {}

        has_custom = False
        for hash_k, hash_v in clean_dict.items():
            hash_alg = stix2.hashes.infer_hash_algorithm(hash_k)

            if hash_alg:
                # Library-supported hash algorithm: sanity check the value.
                if not stix2.hashes.check_hash(hash_alg, hash_v):
                    raise ValueError(
                        "'{0}' is not a valid {1} hash".format(
                            hash_v, hash_alg.name,
                        ),
                    )

                spec_name = self.__alg_to_spec_name.get(hash_alg)
                if not spec_name:
                    # There is library support for the hash algorithm, but it's
                    # not in the spec.  So it's custom.  Just use the user's
                    # name as-is.
                    has_custom = True
                    spec_name = hash_k

            else:
                # Unrecognized hash algorithm; use as-is.  Hash algorithm name
                # must be an exact match from spec, or it will be considered
                # custom.
                spec_name = hash_k
                if spec_name not in self.__spec_hash_names:
                    has_custom = True

            if not allow_custom and has_custom:
                raise CustomContentError(
                    "custom hash algorithm: " + hash_k,
                )

            spec_dict[spec_name] = hash_v

        return spec_dict, has_custom


class BinaryProperty(Property):

    def clean(self, value, allow_custom=False):
        try:
            base64.b64decode(value)
        except (binascii.Error, TypeError):
            raise ValueError("must contain a base64 encoded string")
        return value, False


class HexProperty(Property):

    def clean(self, value, allow_custom=False):
        if not re.match(r"^([a-fA-F0-9]{2})+$", value):
            raise ValueError("must contain an even number of hexadecimal characters")
        return value, False


class ReferenceProperty(Property):

    _OBJECT_CATEGORIES = {"SDO", "SCO", "SRO"}
    _WHITELIST, _BLACKLIST = range(2)

    def __init__(self, valid_types=None, invalid_types=None, spec_version=stix2.DEFAULT_VERSION, **kwargs):
        """
        references sometimes must be to a specific object type
        """
        self.spec_version = spec_version
        self.stix_vid = "v" + self.spec_version.replace(".", "")

        if (valid_types is not None and invalid_types is not None) or \
                (valid_types is None and invalid_types is None):
            raise ValueError(
                "Exactly one of 'valid_types' and 'invalid_types' must be "
                "given",
            )

        if valid_types and not isinstance(valid_types, list):
            valid_types = [valid_types]
        elif invalid_types and not isinstance(invalid_types, list):
            invalid_types = [invalid_types]

        if valid_types is not None and len(valid_types) == 0:
            raise ValueError("Impossible type constraint: empty whitelist")

        self.types = set(valid_types or invalid_types)
        self.auth_type = self._WHITELIST if valid_types else self._BLACKLIST

        super(ReferenceProperty, self).__init__(**kwargs)

    def clean(self, value, allow_custom):
        if isinstance(value, _STIXBase):
            value = value.id
        value = str(value)

        _validate_id(value, self.spec_version, None)

        obj_type = value[:value.index('--')]

        # Only comes into play when inverting a hybrid whitelist.
        # E.g. if the possible generic categories are A, B, C, then the
        # inversion of whitelist constraint "A or x" (where x is a specific
        # type) is something like "[not (B or C)] or x".  In other words, we
        # invert the generic categories to produce a blacklist, but leave the
        # specific categories alone; they essentially become exceptions to our
        # blacklist.
        blacklist_exceptions = set()

        generics = self.types & self._OBJECT_CATEGORIES
        specifics = self.types - generics
        auth_type = self.auth_type
        if allow_custom and auth_type == self._WHITELIST and generics:
            # If allowing customization and using a whitelist, and if generic
            # "category" types were given, we need to allow custom object types
            # of those categories.  Unless registered, it's impossible to know
            # whether a given type is within a given category.  So we take a
            # permissive approach and allow any type which is not known to be
            # in the wrong category.  I.e. flip the whitelist set to a
            # blacklist of a complementary set.
            auth_type = self._BLACKLIST
            generics = self._OBJECT_CATEGORIES - generics
            blacklist_exceptions, specifics = specifics, blacklist_exceptions

        if auth_type == self._WHITELIST:
            type_ok = _type_in_generic_set(obj_type, generics, self.stix_vid) \
                or obj_type in specifics

        else:
            type_ok = (
                not _type_in_generic_set(
                    obj_type, generics, self.stix_vid,
                )
                and obj_type not in specifics
            ) or obj_type in blacklist_exceptions

        if not type_ok:
            raise ValueError(
                "The type-specifying prefix '%s' for this property is not "
                "valid" % obj_type,
            )

        # We need to figure out whether the referenced object is custom or
        # not.  No good way to do that at present... just check if
        # unregistered and for the "x-" type prefix, for now?
        has_custom = not _type_in_generic_set(
            obj_type, self._OBJECT_CATEGORIES, self.stix_vid,
        ) or obj_type.startswith("x-")

        if not allow_custom and has_custom:
            raise CustomContentError(
                "reference to custom object type: " + obj_type,
            )

        return value, has_custom


def _type_in_generic_set(type_, type_set, stix_vid):
    """
    Determine if type_ is in the given set, with respect to the given STIX
    version.  This handles special generic category values "SDO", "SCO",
    "SRO", so it's not a simple set containment check.  The type_set is
    implicitly "OR"d.
    """
    type_maps = STIX2_OBJ_MAPS[stix_vid]

    result = False
    for type_id in type_set:
        if type_id == "SDO":
            result = type_ in type_maps["objects"] and type_ not in [
                "relationship", "sighting",
            ]  # sigh
        elif type_id == "SCO":
            result = type_ in type_maps["observables"]
        elif type_id == "SRO":
            result = type_ in ["relationship", "sighting"]
        else:
            raise ValueError("Unrecognized generic type category: " + type_id)

        if result:
            break

    return result


SELECTOR_REGEX = re.compile(r"^([a-z0-9_-]{3,250}(\.(\[\d+\]|[a-z0-9_-]{1,250}))*|id)$")


class SelectorProperty(Property):

    def clean(self, value, allow_custom=False):
        if not SELECTOR_REGEX.match(value):
            raise ValueError("must adhere to selector syntax.")
        return value, False


class ObjectReferenceProperty(StringProperty):

    def __init__(self, valid_types=None, **kwargs):
        if valid_types and type(valid_types) is not list:
            valid_types = [valid_types]
        self.valid_types = valid_types
        super(ObjectReferenceProperty, self).__init__(**kwargs)


class EmbeddedObjectProperty(Property):

    def __init__(self, type, **kwargs):
        self.type = type
        super(EmbeddedObjectProperty, self).__init__(**kwargs)

    def clean(self, value, allow_custom):
        if isinstance(value, dict):
            value = self.type(allow_custom=allow_custom, **value)
        elif not isinstance(value, self.type):
            raise ValueError("must be of type {}.".format(self.type.__name__))

        has_custom = False
        if isinstance(value, _STIXBase):
            has_custom = value.has_custom

        if not allow_custom and has_custom:
            raise CustomContentError("custom content encountered")

        return value, has_custom


class EnumProperty(StringProperty):
    """
    Used for enumeration type properties.  Properties of this type do not allow
    customization.
    """

    def __init__(self, allowed, **kwargs):
        if isinstance(allowed, str):
            allowed = [allowed]
        self.allowed = allowed
        super(EnumProperty, self).__init__(**kwargs)

    def clean(self, value, allow_custom):
        cleaned_value, _ = super(EnumProperty, self).clean(value, allow_custom)

        if cleaned_value not in self.allowed:
            raise ValueError("value '{}' is not valid for this enumeration.".format(cleaned_value))

        return cleaned_value, False


class OpenVocabProperty(StringProperty):
    """
    Used for open vocab type properties.
    """

    def __init__(self, allowed, **kwargs):
        super(OpenVocabProperty, self).__init__(**kwargs)

        if isinstance(allowed, str):
            allowed = [allowed]
        self.allowed = allowed

    def clean(self, value, allow_custom):
        cleaned_value, _ = super(OpenVocabProperty, self).clean(
            value, allow_custom,
        )

        has_custom = cleaned_value not in self.allowed

        if not allow_custom and has_custom:
            raise CustomContentError(
                "custom value in open vocab: '{}'".format(cleaned_value),
            )

        return cleaned_value, has_custom


class PatternProperty(StringProperty):
    pass


class ObservableProperty(Property):
    """Property for holding Cyber Observable Objects.
    """

    def __init__(self, spec_version=stix2.DEFAULT_VERSION, *args, **kwargs):
        self.spec_version = spec_version
        super(ObservableProperty, self).__init__(*args, **kwargs)

    def clean(self, value, allow_custom):
        try:
            dictified = _get_dict(value)
            # get deep copy since we are going modify the dict and might
            # modify the original dict as _get_dict() does not return new
            # dict when passed a dict
            dictified = copy.deepcopy(dictified)
        except ValueError:
            raise ValueError("The observable property must contain a dictionary")
        if dictified == {}:
            raise ValueError("The observable property must contain a non-empty dictionary")

        valid_refs = dict((k, v['type']) for (k, v) in dictified.items())

        has_custom = False
        for key, obj in dictified.items():
            parsed_obj = parse_observable(
                obj,
                valid_refs,
                allow_custom=allow_custom,
                version=self.spec_version,
            )

            if isinstance(parsed_obj, _STIXBase):
                has_custom = has_custom or parsed_obj.has_custom
            else:
                # we get dicts for unregistered custom objects
                has_custom = True

            if not allow_custom and has_custom:
                if parsed_obj.has_custom:
                    raise CustomContentError(
                        "customized {} observable found".format(
                            parsed_obj["type"],
                        ),
                    )

            dictified[key] = parsed_obj

        return dictified, has_custom


class ExtensionsProperty(DictionaryProperty):
    """Property for representing extensions on Observable objects.
    """

    def __init__(self, spec_version=stix2.DEFAULT_VERSION, enclosing_type=None, required=False):
        self.enclosing_type = enclosing_type
        super(ExtensionsProperty, self).__init__(spec_version=spec_version, required=required)

    def clean(self, value, allow_custom):
        try:
            dictified = _get_dict(value)
            # get deep copy since we are going modify the dict and might
            # modify the original dict as _get_dict() does not return new
            # dict when passed a dict
            dictified = copy.deepcopy(dictified)
        except ValueError:
            raise ValueError("The extensions property must contain a dictionary")

        v = 'v' + self.spec_version.replace('.', '')

        has_custom = False
        specific_type_map = STIX2_OBJ_MAPS[v]['observable-extensions'].get(self.enclosing_type, {})
        for key, subvalue in dictified.items():
            if key in specific_type_map:
                cls = specific_type_map[key]
                if type(subvalue) is dict:
                    ext = cls(allow_custom=allow_custom, **subvalue)
                elif type(subvalue) is cls:
                    # If already an instance of an _Extension class, assume it's valid
                    ext = subvalue
                else:
                    raise ValueError("Cannot determine extension type.")

                has_custom = has_custom or ext.has_custom

                if not allow_custom and has_custom:
                    raise CustomContentError(
                        "custom content found in {} extension".format(
                            key,
                        ),
                    )

                dictified[key] = ext

            else:
                if allow_custom:
                    has_custom = True
                    dictified[key] = subvalue
                else:
                    raise CustomContentError("Can't parse unknown extension type: {}".format(key))

        return dictified, has_custom


class STIXObjectProperty(Property):

    def __init__(self, spec_version=stix2.DEFAULT_VERSION, *args, **kwargs):
        self.spec_version = spec_version
        super(STIXObjectProperty, self).__init__(*args, **kwargs)

    def clean(self, value, allow_custom):
        # Any STIX Object (SDO, SRO, or Marking Definition) can be added to
        # a bundle with no further checks.
        if any(x in ('_DomainObject', '_RelationshipObject', 'MarkingDefinition')
               for x in get_class_hierarchy_names(value)):
            # A simple "is this a spec version 2.1+ object" test.  For now,
            # limit 2.0 bundles to 2.0 objects.  It's not possible yet to
            # have validation co-constraints among properties, e.g. have
            # validation here depend on the value of another property
            # (spec_version).  So this is a hack, and not technically spec-
            # compliant.
            if 'spec_version' in value and self.spec_version == '2.0':
                raise ValueError(
                    "Spec version 2.0 bundles don't yet support "
                    "containing objects of a different spec "
                    "version.",
                )

            if not allow_custom and value.has_custom:
                raise CustomContentError("custom content encountered")

            return value, value.has_custom
        try:
            dictified = _get_dict(value)
        except ValueError:
            raise ValueError("This property may only contain a dictionary or object")
        if dictified == {}:
            raise ValueError("This property may only contain a non-empty dictionary or object")
        if 'type' in dictified and dictified['type'] == 'bundle':
            raise ValueError("This property may not contain a Bundle object")
        if 'spec_version' in dictified and self.spec_version == '2.0':
            # See above comment regarding spec_version.
            raise ValueError(
                "Spec version 2.0 bundles don't yet support "
                "containing objects of a different spec version.",
            )

        parsed_obj = parse(dictified, allow_custom=allow_custom)

        if isinstance(parsed_obj, _STIXBase):
            has_custom = parsed_obj.has_custom
        else:
            # we get dicts for unregistered custom objects
            has_custom = True

        if not allow_custom and has_custom:
            # parse() will ignore the caller's allow_custom=False request if
            # the object type is registered and dictified has a
            # "custom_properties" key.  So we have to do another check here.
            raise CustomContentError(
                "customized {} object found".format(
                    parsed_obj["type"],
                ),
            )

        return parsed_obj, has_custom
