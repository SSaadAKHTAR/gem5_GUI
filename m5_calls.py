import inspect
import logging
import os
import sys

from gui_views.state import get_path

get_path()
sys.path.append(os.getenv('gem5_path'))

from common import ObjectList
import m5.objects
from m5.objects import *
from m5.params import *
from m5.proxy import AttrProxy


def portsCompatible(parent, child):
    """ Checks if two port values are compatible for connections """
    compatible = False
    try:
        compatible = Port.is_compat(parent, child)
    except:
        e = sys.exc_info()[0]
        logging.error("Error on port connection %s" % e.__name__)
    return compatible


def getPortInfo(m5_object):
    """ Given a gem5 object class create a dictionary containing info on
        the objects ports"""
    port_dict = {} #Save port info for the objects
    for port_name, port in m5_object._ports.items():
        port_attr = {"Description": port.desc, 'Name': port_name, \
        'Value': port, 'Default': port, 'Type': Port}
        port_dict[port_name] = port_attr
    return port_dict

def getParamInfo(m5_object):
    """ Given a gem5 object class create a dictionary containing info on
        the objects parameter."""
    param_dict = {}
    for pname, param in m5_object._params.items():
        param_attr = {'Description': param.desc, 'Type': param.ptype}
        if hasattr(param, 'default'):
            param_attr["Default"] = param.default
            param_attr["Value"] = param.default
        else:
            param_attr["Default"] = None
            param_attr["Value"] = None
        param_dict[pname] = param_attr
    return param_dict

def objTreePreprocessing(obj_tree):
    """ Any preprocessing on the paramters or ports for any gem5 object is
        done in this fn before passing it to the gui"""
    # Root has a default value for eventq_indexthat referecnes a Parent which
    #   does not fit with our logic. So we set it to the default value if you
    #   call the root constructor, which is 0
    obj_tree['Root']['Root']['params']['eventq_index']['Default'] = 0
    obj_tree['Root']['Root']['params']['eventq_index']['Value'] = 0


def get_obj_lists():
    """ Given a set of predertimened base classes creates two dictionaries used
        for metadata purposes in the gui.

        The first is a nested dictionary which maps base object names to
        another dictionary which maps the sub-object name to a dictionary
        containing parameter info.

        The second dictionary maps an object's name to the actual class in
        m5.objects to be used for instantiating objects.
        """
    obj_tree = {}
    instance_tree = {}
    # set_subtypes is used to not double count objects already in the dicts
    set_subtypes = set()

    #TODO this list is predetermined, must compile final list of all objects
    categories = ['BaseXBar', 'BranchPredictor', 'BaseCPU', 'BasePrefetcher',
       'IndirectPredictor', 'BaseCache', 'DRAMCtrl', 'Root', 'BaseInterrupts',
         'SimObject']

    for base_obj in categories:
        # Create ObjectLists for each base element
        obj_list = ObjectList.ObjectList(getattr(m5.objects, base_obj, None))
        set_subtypes.add(base_obj)

        sub_objs = {}  # Go through each derived class in the Object List
        for sub_obj_name, sub_obj_inst  in obj_list._sub_classes.items():
            if base_obj == 'SimObject':
                # SimObject will have all objects as derived classes so we
                #   need to make sure we don't double count. This is why
                #   SimObject is the last element in categories
                if sub_obj_name in set_subtypes:
                    continue
            else:
                set_subtypes.add(sub_obj_name)

            #Save the object instance
            instance_tree[sub_obj_name] = sub_obj_inst
            port_dict = getPortInfo(obj_list._sub_classes[sub_obj_name])
            param_dict = getParamInfo(obj_list._sub_classes[sub_obj_name])
            sub_objs[sub_obj_name] = {'params': param_dict, 'ports': port_dict}

        if base_obj == 'SimObject':
            base_obj = 'Other'
        obj_tree[base_obj] = sub_objs

    objTreePreprocessing(obj_tree)

    return obj_tree, instance_tree

def isSimObjectParam(param_info):
    """ Given metadata of parameter see if it is a SimObject Param"""
    return issubclass(param_info["Type"], SimObject)

def setParamValue(simobject, symobject, param, param_info, m5_children):
    """ Set a certain param for the simobject instance. This entails multiple
        checks to see how the value of the param is set """
    # Check if the user changed the parameter's value ie the type of the value
    #   should be unicode
    if isinstance(param_info["Value"], unicode):
        # Check if the param's type is another Simobject, which means
        #   it must be set as a child of the object already
        if isSimObjectParam(param_info):
            for obj in m5_children:
                sym, sim = obj
                if sym == param_info["Value"]:
                    setattr(simobject, param, sim)
                    break
        else:
            # Note: he gem5 compiler seems to handle cases where the value
            # is not another simobject by just comverting the value to a string
            setattr(simobject, param, str(param_info["Value"]))
    else:
        # If the user has not changed and there is a default
        if param_info["Value"] == param_info["Default"]:
            # If the param is an AttrProxy, set it using find
            if isinstance(param_info["Default"], AttrProxy):
                result, found = param_info["Default"].find(simobject)
                if not found:
                    logging.debug("Proxy not found given param " + param \
                    + " for " + symObject.component_name)
                else:
                    param_info["Value"] = result
                setattr(simobject, param, param_info["Value"])
        else:
            if str(param_info["Value"]) in symobject.connected_objects:
                logging.error("object exists and can be parameterized")

def traverseParams(sym_catalog, symobject, simobject):
    """ Recursively goes through object tree starting at symobject and
    set parameters and child objects for corresponding simobject instance"""

    # Setting the connections for the child objects
    # Note: this is done alongside setting parameters in case a parameter
    #   is a simobject, in which case it would look at the child objects
    m5_children = []
    for child in symobject.connected_objects:
        sym = sym_catalog[child].name
        sim = sym_catalog[child].sim_object_instance
        setattr(simobject, sym, sim)
        m5_children.append((sym, sim))

    # Setting the paramerters for the object instance
    for param, param_info in symobject.instance_params.items():
        setParamValue(simobject, symobject, param, param_info, m5_children)

    # Recurse for the objects children
    for m_child in m5_children:
        traverseParams(sym_catalog, sym_catalog[m_child[0]], m_child[1])

    return (symobject.name, simobject)

def setPortValue(port, port_info, sym_catalog, simobject):
    """Create a port connection between two simobjects"""
    if isinstance(port_info["Value"], str):
        values = port_info["Value"].split(".")
        #set port value, ex: values = ['system', 'system_port']
        setattr(simobject, port,\
            getattr(sym_catalog[values[0]].sim_object_instance,values[1]))
    else:
        logging.debug("Port value for " + port + " is not set")

def traversePorts(sym_catalog, symobject, simobject):
    """Traverse object tree starting at simobject and set ports recursively"""
    for port, port_info in symobject.instance_ports.items():
        if isinstance(simobject, list): #for vector param value
            for i in range(len(simobject)):
                setPortValue(port, port_info, sym_catalog, simobject[i])
        else:
            #nonvector param value
            setPortValue(port, port_info, sym_catalog, simobject)

    #set ports for children
    for child in symobject.connected_objects:
        traversePorts(sym_catalog, sym_catalog[child],\
            getattr(simobject, child))

    return symobject.name, simobject

def traverse_hierarchy_root(sym_catalog, symroot):
    """Recursively set parameters and then recursively set ports for
        instantiated objs"""
    simroot = None
    try:
        root = symroot.sim_object_instance
        name, simroot = traverseParams(sym_catalog, symroot, root)
        name, simroot = traversePorts(sym_catalog, symroot, simroot)
    except:
        e = sys.exc_info()[0]
        logging.error("Could not create simobject tree due to %s" % e.__name__)
    return symroot.name, simroot

def get_imported_obs(obj_clss, filename):
    """ A condensed version of get_obj_trees used for simobj classes that
        are imported from a seperate file"""
    mini_tree = {filename: {}}
    instances = {}
    for name, cls in obj_clss:
        if name != 'SimObject':
            port_dict = getPortInfo(cls)
            param_dict = getParamInfo(cls)
            mini_tree[filename][name] = \
                {'params': param_dict, 'ports': port_dict}
            instances[name] = cls
    return mini_tree, instances

def get_debug_flags():
    try:
        return m5.debug.flags
    except:
        e = sys.exc_info()[0]
        logging.error("Erorr returning debug flags %s" % e.__name__)

def instantiate_model():
    """ Instantiate the model on the gui """
    try:
        m5.instantiate()
        return False
    except:
        e = sys.exc_info()[0]
        logging.error("Instantiate error on proxy param by %s" % e.__name__)
        return True


def simulate():
    """ Simulate the model on the gui """
    try:
        exit_event = m5.simulate()
        print('Exiting @ tick %i because %s' %(m5.curTick(), \
            exit_event.getCause()))
        return False
    except:
        e = sys.exc_info()[0]
        logging.error("Simulation error caused by %s" % e.__name__)
        return True


def getRoot():
    """ Singleton type fn for Root simobject instance """
    logging.debug("finding root inst")
    if Root.getInstance() == None:
        return Root()
    else:
        logging.debug("existing inst")
        return Root.getInstance()
