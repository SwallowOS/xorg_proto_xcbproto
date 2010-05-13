'''
This module contains the classes which represent XCB data types.
'''
from expr import Field, Expression
import __main__

class Type(object):
    '''
    Abstract base class for all XCB data types.
    Contains default fields, and some abstract methods.
    '''
    def __init__(self, name):
        '''
        Default structure initializer.  Sets up default fields.

        Public fields:
        name is a tuple of strings specifying the full type name.
        size is the size of the datatype in bytes, or None if variable-sized.
        nmemb is 1 for non-list types, None for variable-sized lists, otherwise number of elts.
        booleans for identifying subclasses, because I can't figure out isinstance().
        '''
        self.name = name
        self.size = None
        self.nmemb = None
        self.resolved = False

        # Screw isinstance().
        self.is_simple = False
        self.is_list = False
        self.is_expr = False
        self.is_container = False
        self.is_reply = False
        self.is_union = False
        self.is_pad = False

    def resolve(self, module):
        '''
        Abstract method for resolving a type.
        This should make sure any referenced types are already declared.
        '''
        raise Exception('abstract resolve method not overridden!')

    def out(self, name):
        '''
        Abstract method for outputting code.
        These are declared in the language-specific modules, and
        there must be a dictionary containing them declared when this module is imported!
        '''
        raise Exception('abstract out method not overridden!')

    def fixed_size(self):
        '''
        Abstract method for determining if the data type is fixed-size.
        '''
        raise Exception('abstract fixed_size method not overridden!')

    def make_member_of(self, module, complex_type, field_type, field_name, visible, wire, auto):
        '''
        Default method for making a data type a member of a structure.
        Extend this if the data type needs to add an additional length field or something.

        module is the global module object.
        complex_type is the structure object.
        see Field for the meaning of the other parameters.
        '''
        new_field = Field(self, field_type, field_name, visible, wire, auto)

        # We dump the _placeholder_byte if any fields are added.
        for (idx, field) in enumerate(complex_type.fields):
            if field == _placeholder_byte:
                complex_type.fields[idx] = new_field
                return

        complex_type.fields.append(new_field)

class SimpleType(Type):
    '''
    Derived class which represents a cardinal type like CARD32 or char.
    Any type which is typedef'ed to cardinal will be one of these.

    Public fields added:
    none
    '''
    def __init__(self, name, size):
        Type.__init__(self, name)
        self.is_simple = True
        self.size = size
        self.nmemb = 1

    def resolve(self, module):
        self.resolved = True

    def fixed_size(self):
        return True

    out = __main__.output['simple']


# Cardinal datatype globals.  See module __init__ method.
tcard8 = SimpleType(('uint8_t',), 1)
tcard16 = SimpleType(('uint16_t',), 2)
tcard32 = SimpleType(('uint32_t',), 4)
tint8 =  SimpleType(('int8_t',), 1)
tint16 = SimpleType(('int16_t',), 2)
tint32 = SimpleType(('int32_t',), 4)
tchar =  SimpleType(('char',), 1)
tfloat = SimpleType(('float',), 4)
tdouble = SimpleType(('double',), 8)


class Enum(SimpleType):
    '''
    Derived class which represents an enum.  Fixed-size.

    Public fields added:
    values contains a list of (name, value) tuples.  value is empty, or a number.
    bits contains a list of (name, bitnum) tuples.  items only appear if specified as a bit. bitnum is a number.
    '''
    def __init__(self, name, elt):
        SimpleType.__init__(self, name, 4)
        self.values = []
        self.bits = []
        for item in list(elt):
            # First check if we're using a default value
            if len(list(item)) == 0:
                self.values.append((item.get('name'), ''))
                continue

            # An explicit value or bit was specified.
            value = list(item)[0]
            if value.tag == 'value':
                self.values.append((item.get('name'), value.text))
            elif value.tag == 'bit':
                self.values.append((item.get('name'), '%u' % (1 << int(value.text, 0))))
                self.bits.append((item.get('name'), value.text))

    def resolve(self, module):
        self.resolved = True

    def fixed_size(self):
        return True

    out = __main__.output['enum']


class ListType(Type):
    '''
    Derived class which represents a list of some other datatype.  Fixed- or variable-sized.

    Public fields added:
    member is the datatype of the list elements.
    parent is the structure type containing the list.
    expr is an Expression object containing the length information, for variable-sized lists.
    '''
    def __init__(self, elt, member, parent):
        Type.__init__(self, member.name)
        self.is_list = True
        self.member = member
        self.parent = parent

        if elt.tag == 'list':
            elts = list(elt)
            self.expr = Expression(elts[0] if len(elts) else elt, self)
        elif elt.tag == 'valueparam':
            self.expr = Expression(elt, self)

        self.size = member.size if member.fixed_size() else None
        self.nmemb = self.expr.nmemb if self.expr.fixed_size() else None

    def make_member_of(self, module, complex_type, field_type, field_name, visible, wire, auto):
        if not self.fixed_size():
            # We need a length field.
            # Ask our Expression object for it's name, type, and whether it's on the wire.
            lenfid = self.expr.lenfield_type
            lenfield_name = self.expr.lenfield_name
            lenwire = self.expr.lenwire
            needlen = True

            # See if the length field is already in the structure.
            for field in self.parent.fields:
                if field.field_name == lenfield_name:
                    needlen = False

            # It isn't, so we need to add it to the structure ourself.
            if needlen:
                type = module.get_type(lenfid)
                lenfield_type = module.get_type_name(lenfid)
                type.make_member_of(module, complex_type, lenfield_type, lenfield_name, True, lenwire, False)

        # Add ourself to the structure by calling our original method.
        Type.make_member_of(self, module, complex_type, field_type, field_name, visible, wire, auto)

    def resolve(self, module):
        if self.resolved:
            return
        self.member.resolve(module)

        # Find my length field again.  We need the actual Field object in the expr.
        # This is needed because we might have added it ourself above.
        if not self.fixed_size():
            for field in self.parent.fields:
                if field.field_name == self.expr.lenfield_name and field.wire:
                    self.expr.lenfield = field
                    break
            
        self.resolved = True

    def fixed_size(self):
        return self.member.fixed_size() and self.expr.fixed_size()

class ExprType(Type):
    '''
    Derived class which represents an exprfield.  Fixed size.

    Public fields added:
    expr is an Expression object containing the value of the field.
    '''
    def __init__(self, elt, member, parent):
        Type.__init__(self, member.name)
        self.is_expr = True
        self.member = member
        self.parent = parent

        self.expr = Expression(list(elt)[0], self)

        self.size = member.size
        self.nmemb = 1

    def resolve(self, module):
        if self.resolved:
            return
        self.member.resolve(module)
        self.resolved = True

    def fixed_size(self):
        return True

class PadType(Type):
    '''
    Derived class which represents a padding field.
    '''
    def __init__(self, elt):
        Type.__init__(self, tcard8.name)
        self.is_pad = True
        self.size = 1
        self.nmemb = 1 if (elt == None) else int(elt.get('bytes'), 0)

    def resolve(self, module):
        self.resolved = True

    def fixed_size(self):
        return True

    
class ComplexType(Type):
    '''
    Derived class which represents a structure.  Base type for all structure types.

    Public fields added:
    fields is an array of Field objects describing the structure fields.
    '''
    def __init__(self, name, elt):
        Type.__init__(self, name)
        self.is_container = True
        self.elt = elt
        self.fields = []
        self.nmemb = 1
        self.size = 0

    def resolve(self, module):
        if self.resolved:
            return
        pads = 0

        # Resolve all of our field datatypes.
        for child in list(self.elt):
            if child.tag == 'pad':
                field_name = 'pad' + str(pads)
                fkey = 'CARD8'
                type = PadType(child)
                pads = pads + 1
                visible = False
            elif child.tag == 'field':
                field_name = child.get('name')
                fkey = child.get('type')
                type = module.get_type(fkey)
                visible = True
            elif child.tag == 'exprfield':
                field_name = child.get('name')
                fkey = child.get('type')
                type = ExprType(child, module.get_type(fkey), self)
                visible = False
            elif child.tag == 'list':
                field_name = child.get('name')
                fkey = child.get('type')
                type = ListType(child, module.get_type(fkey), self)
                visible = True
            elif child.tag == 'valueparam':
                field_name = child.get('value-list-name')
                fkey = 'CARD32'
                type = ListType(child, module.get_type(fkey), self)
                visible = True
            else:
                # Hit this on Reply
                continue 

            # Get the full type name for the field
            field_type = module.get_type_name(fkey)
            # Add the field to ourself
            type.make_member_of(module, self, field_type, field_name, visible, True, False)
            # Recursively resolve the type (could be another structure, list)
            type.resolve(module)

        self.calc_size() # Figure out how big we are
        self.resolved = True

    def calc_size(self):
        self.size = 0
        for m in self.fields:
            if not m.wire:
                continue
            if m.type.fixed_size():
                self.size = self.size + (m.type.size * m.type.nmemb)
            else:
                self.size = None
                break

    def fixed_size(self):
        for m in self.fields:
            if not m.type.fixed_size():
                return False
        return True


class Struct(ComplexType):
    '''
    Derived class representing a struct data type.
    '''
    out = __main__.output['struct']


class Union(ComplexType):
    '''
    Derived class representing a union data type.
    '''
    def __init__(self, name, elt):
        ComplexType.__init__(self, name, elt)
        self.is_union = True

    out = __main__.output['union']


class Reply(ComplexType):
    '''
    Derived class representing a reply.  Only found as a field of Request.
    '''
    def __init__(self, name, elt):
        ComplexType.__init__(self, name, elt)
        self.is_reply = True

    def resolve(self, module):
        if self.resolved:
            return
        # Add the automatic protocol fields
        self.fields.append(Field(tcard8, tcard8.name, 'response_type', False, True, True))
        self.fields.append(_placeholder_byte)
        self.fields.append(Field(tcard16, tcard16.name, 'sequence', False, True, True))
        self.fields.append(Field(tcard32, tcard32.name, 'length', False, True, True))
        ComplexType.resolve(self, module)
        

class Request(ComplexType):
    '''
    Derived class representing a request.

    Public fields added:
    reply contains the reply datatype or None for void requests.
    opcode contains the request number.
    '''
    def __init__(self, name, elt):
        ComplexType.__init__(self, name, elt)
        self.reply = None
        self.opcode = elt.get('opcode')

        for child in list(elt):
            if child.tag == 'reply':
                self.reply = Reply(name, child)

    def resolve(self, module):
        if self.resolved:
            return
        # Add the automatic protocol fields
        if module.namespace.is_ext:
            self.fields.append(Field(tcard8, tcard8.name, 'major_opcode', False, True, True))
            self.fields.append(Field(tcard8, tcard8.name, 'minor_opcode', False, True, True))
            self.fields.append(Field(tcard16, tcard16.name, 'length', False, True, True))
            ComplexType.resolve(self, module)
        else:
            self.fields.append(Field(tcard8, tcard8.name, 'major_opcode', False, True, True))
            self.fields.append(_placeholder_byte)
            self.fields.append(Field(tcard16, tcard16.name, 'length', False, True, True))
            ComplexType.resolve(self, module)

        if self.reply:
            self.reply.resolve(module)

    out = __main__.output['request']


class Event(ComplexType):
    '''
    Derived class representing an event data type.

    Public fields added:
    opcodes is a dictionary of name -> opcode number, for eventcopies.
    '''
    def __init__(self, name, elt):
        ComplexType.__init__(self, name, elt)
        self.opcodes = {}

        tmp = elt.get('no-sequence-number')
        self.has_seq = (tmp == None or tmp.lower() == 'false' or tmp == '0')
            
    def add_opcode(self, opcode, name, main):
        self.opcodes[name] = opcode
        if main:
            self.name = name

    def resolve(self, module):
        if self.resolved:
            return

        # Add the automatic protocol fields
        self.fields.append(Field(tcard8, tcard8.name, 'response_type', False, True, True))
        if self.has_seq:
            self.fields.append(_placeholder_byte)
            self.fields.append(Field(tcard16, tcard16.name, 'sequence', False, True, True))
        ComplexType.resolve(self, module)

    out = __main__.output['event']


class Error(ComplexType):
    '''
    Derived class representing an error data type.

    Public fields added:
    opcodes is a dictionary of name -> opcode number, for errorcopies.
    '''
    def __init__(self, name, elt):
        ComplexType.__init__(self, name, elt)
        self.opcodes = {}

    def add_opcode(self, opcode, name, main):
        self.opcodes[name] = opcode
        if main:
            self.name = name

    def resolve(self, module):
        if self.resolved:
            return

        # Add the automatic protocol fields
        self.fields.append(Field(tcard8, tcard8.name, 'response_type', False, True, True))
        self.fields.append(Field(tcard8, tcard8.name, 'error_code', False, True, True))
        self.fields.append(Field(tcard16, tcard16.name, 'sequence', False, True, True))
        ComplexType.resolve(self, module)

    out = __main__.output['error']

_placeholder_byte = Field(PadType(None), tcard8.name, 'pad0', False, True, False)
