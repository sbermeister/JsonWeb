"""
Sometimes it would be nice to have :func:`json.loads` return class instances. For example if you
do something like this ::

    person = json.loads('{"__type__": "Person", "first_name": "Shawn", "last_name": "Adams"}')
    
it would be pretty cool if instead of ``person`` being a :class:`dict` it was an instance of a class we defined called :class:`Person`.
Luckily the python standard :mod:`json` module provides support for *class hinting* in the form of the ``object_hook`` keyword
argument accepted by :func:`json.loads`.

The code in :mod:`jsonweb.decode` uses this ``object_hook`` interface to accomplish the awesomeness you are about to witness.
Lets turn that ``person`` :class:`dict` into a proper :class:`Person` instance. ::

    >>> from jsonweb.decode import from_obj, loader

    >>> @from_object()
    ... class Person(object):
    ...     def __init__(self, first_name, last_name):
    ...         self.first_name = first_name
    ...         self.last_name = last_name

    >>> person_json = '{"__type__": "Person", "first_name": "Shawn", "last_name": "Adams"}'
    >>> person = loader(person_json)

    >>> print type(person)
    <class 'Person'>
    >>> print person.first_name
    "Shawn"

But how was :mod:`jsonweb` able to determine how to instantiate the :class:`Person` class? Take a look 
at the :func:`from_object` decorator for a detailed explanation.
"""

import inspect
import json
import copy
from datetime import datetime
from jsonweb.exceptions import JsonWebError

_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S"

class JsonDecodeError(JsonWebError):
    """
    Raised for mailformed json.
    """
    def __init__(self, message, error_sub_type=None, **extras):
        JsonWebError.__init__(self, message, "JSON_PARSE_ERROR", error_sub_type, **extras)
        
class ObjectDecodeError(JsonWebError):
    """
    Raisded when python containers (dicts and lists) cannot be decoded into
    complex types. These exceptions are raised from within an ObjectHook instance.
    """
    def __init__(self, message, error_sub_type, **extras):
        JsonWebError.__init__(self, message, "DATA_DECODE_ERROR", error_sub_type, **extras)

class ObjectAttributeError(ObjectDecodeError):
    def __init__(self, obj_type, attr):
        ObjectDecodeError.__init__(self, 
            "Missing %s attribute for %s." % (attr, obj_type), 
            "MISSING_ATTRIBUTE",
            obj_type=obj_type, 
            attribute=attr
        )

class ObjectNotFoundError(ObjectDecodeError):
    def __init__(self, obj_type):
        ObjectDecodeError.__init__(self, 
            "Cannot decode object %s. No such object." % obj_type, 
            "OBJECT_NOT_FOUND",
            obj_type=obj_type,
        )

def json_request(func):
    """
    Decorate pyramid view callables that require a decoded json request.  If the request
    content_type is application/json it will attempt to decode the string in request.body
    as JSON. The python data structure will be set on the request obj as "decoded_obj"
    Raises BadRequestError otherwise.
    """
    def wrapper(request):
        if not request.content_type == "application/json":
            raise BadRequestError("Missing JSON data")
        
        try:
            obj = json.load(request.body_file, object_hook=object_hook)
        except ValueError, e:
            raise JsonDecodeError(e.args[0])
        request.decoded_obj = obj
        
        return func(request)
    
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper

class JsonWebObjectHandler(object):
    def __init__(self, args, kw_args=None):
        self.args = args
        self.kw_args = kw_args
        
    def __call__(self, cls, obj):
        cls_args = []
        cls_kw_args = {}
        
        for arg in self.args:
            cls_args.append(obj[arg])
            
        if self.kw_args:
            for key, default in self.kw_args:
                cls_kw_args[key] = obj.get(key, default)
        
        return cls(*cls_args, **cls_kw_args)
    
class _ObjectHandlers(object):
    def __init__(self):
        self.__handlers = {}
        
    def add_handler(self, cls, handler, type_name=None, schema=None):
        self.__handlers[type_name or cls.__name__] = (handler, cls, schema)
        
    def get(self, name):
        """
        Get a hanlder tuple. Return None if
        no such handler.
        """
        return self.__handlers.get(name)
    
    def set(self, name, handler_tuple):
        """
        Add a hanlder tuple (handler, cls, schema)
        """
        print name, handler_tuple
        self.__handlers[name] = handler_tuple
    
    def clear(self):
        self.__handlers = {}
        
    def update_handler(self, name, cls=None, handler=None, schema=None):
        # @@ Catch key errors?
        handler_tuple = self[name]
        self.set(name, tuple([
            handler or handler_tuple[0],
            cls or handler_tuple[1],
            schema or handler_tuple[2]            
        ]))
        
    def copy(self):
        handler_copy = _ObjectHandlers()
        [handler_copy.set(n,t) for n,t in self]
        return handler_copy
    
    def __contains__(self, handler_name):
        return handler_name in self.__handlers
    
    def __getitem__(self, handler):
        return self.__handlers[handler]
    
    def __iter__(self):
        for name, handler_tuple in self.__handlers.iteritems():
            yield name, handler_tuple
        
class ObjectHook(object):
    """
    This class does most of the work in managing the handlers that decode the json into python
    class instances. You should not need to  use this class directly. :func:`object_hook` is responsible
    for instiating and using it.
    """
    _DT_FORMAT = _DATETIME_FORMAT
            
    def __init__(self, handlers):
        self.handlers = handlers      
            
    def decode_obj(self, obj):
        """        
        This method is called for every dict decoded in a json string. The presense
        of the key ``__type__`` in ``obj`` will trigger a lookup in ``self.handlers``. If a handler is
        not found for ``__type__`` then an :exc:`ObjectNotFoundError` is raised. If a handler is found it will
        be called with ``obj`` as it only argument. If all goes well it should return a new
        python instant of type ``__type__``.        
        """
        if "__type__" not in obj:
            return obj
        
        obj_type = obj["__type__"]
        try:
            factory, cls, schema = self.handlers[obj_type]
        except KeyError:
            raise ObjectNotFoundError(obj_type)
                
        if schema:
            obj = schema().validate(obj)
        try:
            return factory(cls, obj)
        except KeyError, e:
            raise ObjectAttributeError(obj_type, e.args[0])
        
    def _datetime(self, dt):
        if dt is None:
            return
        try:
            return datetime.strptime(dt, self._DT_FORMAT)
        except TypeError, e:
            raise ObjectDecodeError("datetime attribute for %s must be a string, not %s" % (self.obj, type(dt)))
        except ValueError, e:
            raise ObjectDecodeError(e.args[0])
        
def get_arg_spec(func):
    arg_spec = inspect.getargspec(func)
    args = arg_spec.args
    
    try:   
        if args[0] == "self":
            del args[0]
    except IndexError:
        pass
    
    if not args:
        return None    
    
    kw_args = []
    if arg_spec.defaults:
        for default in reversed(arg_spec.defaults):
            kw_args.append((args.pop(), default))
        
    return args, kw_args

def get_jsonweb_handler(cls):
    arg_spec = get_arg_spec(cls.__init__)
    if arg_spec is None:
        raise JsonWebError("Unable to generate an object_hook handler from %s's `__init__` method." % cls.__name__)
    args, kw = get_arg_spec(cls.__init__)

    return JsonWebObjectHandler(args, kw or None)

_default_object_handlers = _ObjectHandlers()

def from_object(handler=None, type_name=None, schema=None):
    """
    Decorating a class with :func:`from_object` will allow :func:`json.loads` to return
    instances of that class. 
    
    ``handler`` is a callable that should return your class instance. It 
    receives two arguments, your class and a python dict. Here is an example::
    
        >>> import json
        >>> from jsonweb.decode import from_object, loader
        
        >>> def person_decoder(cls, obj):
        ...    return cls(
        ...        obj["first_name"],
        ...        obj["last_name"]
        ...    )
        
        >>> @from_object(person_decoder)        
        ... class Person(object):
        ...     def __init__(self, first_name, last_name):
        ...         self.first_name
        ...         self.last_name = last_name
        
        >>> person_json = '{"__type__": "Person", "first_name": "Shawn", "last_name": "Adams"}'
        >>> person = loader(person_json)
        >>> person
        <Person object at 0x1007d7550>
        
        >>> person.first_name
        'Shawn'
        
    The ``__type__`` key is very important. Without it :mod:`jsonweb` would not know which
    handler to delegate the python dict to. By default :func:`from_object` assumes ``__type__``
    will be the class's ``__name__`` attribute. You can specify your own value by setting
    the ``type_name`` keyword argument::
    
        @from_object(person_decoder, "PersonObject")
        
    Which means the json string would need to be modified to look like this::
    
        '{"__type__": "PersonObject", "first_name": "Shawn", "last_name": "Adams"}'
        
    If a handler cannot be found for ``__type__`` an exception is raised ::
    
        >>> from josnweb.decode import ObjectNotFoundError
        >>> luke = loader('{"__type__": "Jedi", "name": "Luke"}')
        Traceback (most recent call last):
            ...
        ObjectNotFoundError: Cannot decode object Jedi. No such object.
        
    You may have noticed that ``handler`` is optional. If you do not specify a ``handler`` 
    :mod:`jsonweb` will attempt to generate one. It will inspect your class's ``__init__``
    method. Any positional arguments will be considered required while keyword arguments
    will be optional.
    
    .. warning::
    
        A handler cannot be generated from a method signature containing only ``*args`` and ``**kwargs``.
        The handler would not know which keys to pull out of the python dict.
    
    Lets look at a few examples::
            
        >>> @from_object()
        ... class Person(object):
        ...     def __init__(self, first_name, last_name, gender):
        ...         self.first_name = first_name
        ...         self.last_name = last_name
        ...         self.gender = gender

        >>> person_json = '{"__type__": "Person", "first_name": "Shawn", "last_name": "Adams", "gender": "male"}'
        >>> person = loader(person_json)
        
    What happens if we dont want to specify ``gender``::
            
        >>> person_json = '{"__type__": "Person", "first_name": "Shawn", "last_name": "Adams"}'
        >>> person = loader(person_json)
        Traceback (most recent call last):
            ...        
        ObjectAttributeError: Missing gender attribute for Person.
        
    To make ``gender`` optional it must be a keyword argument::
    
        >>> @from_object()
        ... class Person(object):
        ...     def __init__(self, first_name, last_name, gender=None):
        ...         self.first_name = first_name
        ...         self.last_name = last_name
        ...         self.gender = gender
        
        >>> person_json = '{"__type__": "Person", "first_name": "Shawn", "last_name": "Adams"}'
        >>> person = loader(person_json)
        >>> print person.gender
        None
  
    You can specify a json validator for a class with the ``schema`` keyword agrument. 
    Here is a quick example::
    
        >>> from jsonweb.schema import ObjectSchema, ValidationError
        >>> from jsonweb.schema.validators import String
        >>> class PersonSchema(ObjectSchema):
        ...    first_name = String()
        ...    last_name = String()
        ...    gender = String(optional=True)
        
        >>> @from_object(schema=PersonSchema)
        ... class Person(object):
        ...     def __init__(self, first_name, last_name, gender=None):
        ...         self.first_name = first_name
        ...         self.last_name = last_name
        ...         self.gender = gender
        
        >>> person_json = '{"__type__": "Person", "first_name": 12345, "last_name": "Adams"}'
        >>> try:
        ...     person = loader(person_json)
        ... except ValidationError, e:
        ...     print e.errors["first_name"].message
        Expected str got int instead.
    
    Schemas are useful for validating user supplied json in webservices or other web applications.
    For a detailed explination on using schemas see the :mod:`jsonweb.schema`.
    """
    def wrapper(cls):
        _default_object_handlers.add_handler(
            cls, handler or get_jsonweb_handler(cls), type_name, schema
        )
        return cls
    return wrapper

def object_hook(handlers=None, as_type=None):
    """
    Wrapper around :class:`ObjectHook`. Calling this function
    will configure an instance of :class:`ObjectHook` and return
    a callable suitable for passing to :func:`json.loads` as ``object_hook``.

    If you need to decode a JSON string that does not contain a ``__type__`` key 
    and you know that the JSON represents a certain object or list of objects you 
    can use ``as_type`` to specify it ::
    
        >>> json_str = '{"first_name": "bob", "last_name": "smith"}'
        >>> loader(json_str, as_type="Person")
        <Person object at 0x1007d7550>
        >>> # lists work too
        >>> json_str = '''[
        ...     {"first_name": "bob", "last_name": "smith"},
        ...     {"first_name": "jane", "last_name": "smith"}
        ... ]'''
        >>> loader(json_str, as_type="Person")
        [<Person object at 0x1007d7550>, <Person object at 0x1007d7434>]
        
    .. warning::
    
        ``as_type`` assumes EVERY object handled is of the type specified. So nested dicts
        could break object decoding.
         
    ``handlers`` is a dict with this format::
    
        {"Person": {"cls": Person, "handler": person_decoder, "schema": PersonSchema)}
        
    If you do not wish to decorate your classes with :func:`from_object` you can specify the same
    parameters via the ``handlers`` keyword argument. Here is an exmaple::
        
        >>> class Person(object):
        ...    def __init__(self, first_name, last_name):
        ...        self.first_name = first_name
        ...        self.last_name = last_name
        ...        
        >>> def person_decoder(cls, obj):
        ...    return cls(obj["first_name"], obj["last_name"])
            
        >>> handlers = {"Person": {"cls": Person, "handler": person_decoder}}
        >>> person = loader(json_str, handlers=handlers)
        >>> # Or invoking the object_hook interface ourselves
        >>> person = json.loads(json_str, object_hook=object_hook(handlers))
        
    .. note:: 
    
        If you decorate a class with :func:`from_object` you can override the ``handler`` and ``schema`` values
        later. Here is an example of overriding a schema you defined with :func:`from_object` (some code is
        left out for brevity)::
        
            >>> @from_object(schema=PersonSchema)
            >>> class Person(object):
                ...
                
            >>> # and later on in the code...
            >>> handlers = {"Person": {"schema": NewPersonSchema}}
            >>> person = loader(json_str, handlers=handlers)
            
    If you need to use ``as_type`` or ``handlers`` many times in your code you can forgo using :func:`loader` in
    favor of configuring a "custom" object hook callable. Here is an example ::
                
        >>> my_obj_hook = object_hook(handlers)
        >>> # this call uses custom handlers
        >>> person = json.loads(json_str, object_hook=my_obj_hook)
        >>> # and so does this one ...
        >>> another_person = json.loads(json_str, object_hook=my_obj_hook)                                
    """
    if handlers:
        _object_handlers = _default_object_handlers.copy()
        for name, handler_dict in handlers.iteritems():
            if name in _object_handlers:
                _object_handlers.update_handler(name, **handler_dict)
            else:
                _object_handlers.add_handler(
                    handler_dict.pop('cls'),
                    **handler_dict
                )
    else:
        _object_handlers = _default_object_handlers
               
    decode = ObjectHook(_object_handlers)
    def handler(obj):
        if as_type:
            obj["__type__"] = as_type
        return decode.decode_obj(obj)
    return handler

def loader(json_str, **kw):
    """
    Call this function as you would call :func:`json.loads`. It wraps the :ref:`object_hook` 
    interface and returns python class instances from JSON strings.
    
    :param handlers: is a dict of handlers. see :func:`object_hook`
    
    :param as_type: explicitly specify the type of object the JSON represents. see :func:`object_hook`
            
    :param kw: the rest of the kw args will be passed to the underlying :func:`json.loads` calls.
                
    """
    kw["object_hook"] = object_hook(
        kw.pop("handlers", None),
        kw.pop("as_type", None)        
    )
    return json.loads(json_str, **kw)

