#!/usr/bin/env python
#
# fdict
# Copyright (C) 2017 Larroque Stephen
#
# Licensed under the MIT License (MIT)
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#

import collections
import shelve
import sys
import tempfile


PY3 = (sys.version_info >= (3,0))

if PY3:
    _zip = zip
else:
    import itertools
    _zip = itertools.izip


__all__ = ['fdict', 'sfdict']


class fdict(dict):
    '''Flattened nested dict, all items are settable and gettable through ['item1']['item2'] standard form or ['item1/item2'] internal form.
    This allows to replace the internal dict with any on-disk storage system like a shelve's shelf (great for huge nested dicts that cannot fit into memory).
    Main limitation: an entry can be both a singleton and a nested fdict: when an item is a singleton, you can setitem to replace to a nested dict, but if it is a nested dict and you setitem it to a singleton, both will coexist. Except for fastview mode, there is no way to know if a nested dict exists unless you walk through all items, which would be too consuming for a simple setitem. In this case, a getitem will always return the singleton, but nested leaves can always be accessed via items() or by direct access (eg, x['a/b/c']).
    
    Fastview mode: remove conflicts issue and allow for fast O(m) contains(), delete() and view*() (such as vieitems()) where m in the number of subitems, instead of O(n) where n was the total number of elements in the fdict(). Downside is setitem() being O(m) too because of nodes metadata building, and memory/storage overhead, since we store all nodes and leaves lists in order to allow for fast lookup.
    '''
    def __init__(self, d=None, rootpath='', delimiter='/', fastview=False, **kwargs):
        # Init self parameters
        self.rootpath = rootpath
        self.delimiter = delimiter
        self.fastview = fastview
        self.kwargs = kwargs  # store all kwargs for easy subclassing

        if d is not None:
            if rootpath:
                # Internal call, we get a subdict, we just create a new fdict with the same dictionary but with a restricted rootpath
                if isinstance(d, dict):
                    self.d = d
                else:
                    # sometimes (particularly extract(fullpath=True)) we get a list of tuples instead of a dict
                    self.d = dict(d)
            elif isinstance(d, self.__class__):
                # We were supplied a fdict, initialize a copy
                self.d = d.copy().d
            elif isinstance(d, dict):
                # Else it is not an internal call, the user supplied a dict to initialize the fdict, we have to flatten its keys
                self.d = self.flatkeys(d, sep=delimiter)
                if self.fastview:
                    self._build_metadata(list(self._generickeys(self.d)))
            else:
                # Else the user supplied another type of object, we try to convert to a dict and flatten it
                self.d = self.flatkeys(dict(d), sep=delimiter)
                if self.fastview:
                    self._build_metadata(list(self._generickeys(self.d)))
        else:
            # No dict supplied, create an empty dict
            self.d = dict()

        # Call compatibility layer
        self._viewkeys, self._viewvalues, self._viewitems = self._getitermethods(self.d)

    @staticmethod
    def _getitermethods(d):
        '''Defines what function to use to access the internal dictionary items most efficiently depending on Python version'''
        if PY3:
            # Py3
            _viewkeys = d.keys
            _viewvalues = d.values
            _viewitems = d.items
        else:
            # Py2
            if getattr(d, 'viewvalues', None):
                # Py2.7
                _viewkeys = d.viewkeys
                _viewvalues = d.viewvalues
                _viewitems = d.viewitems
            else:
                # Py2.6
                _viewkeys = d.iterkeys
                _viewvalues = d.itervalues
                _viewitems = d.iteritems
        return _viewkeys, _viewvalues, _viewitems

    def _generickeys(self, d):
        return self._getitermethods(d)[0]()

    def _genericitems(self, d):
        return self._getitermethods(d)[2]()

    @staticmethod
    def _get_all_parent_nodes(path, delimiter='/'):
        '''Get path to all parent nodes for current leaf, starting from leaf's direct parent down to root'''
        pos = path.rfind(delimiter)
        i = 0
        while pos != -1:
            yield path[:pos+1]
            pos = path.rfind(delimiter, 0, pos)

    @staticmethod
    def _get_all_parent_nodes_nested(path, delimiter='/'):
        '''Get path to all parent nodes for current leaf, starting from root down to leaf's direct parent, and return only the relative key (not the fullkey)'''
        pos = path.find(delimiter)
        i = 0
        lastpos = 0
        while pos != -1:
            yield path[lastpos:pos]
            lastpos = pos+1
            pos = path.find(delimiter, pos+1)

    @staticmethod
    def _get_parent_node(path, delimiter='/'):
        '''Get path to the first parent of current leaf'''
        endpos = len(path)  # 'a/b' (leaf)
        if path.endswith(delimiter):  # 'a/b/' (node)
            endpos -= 1
        return path[:path.rfind(delimiter, 0, endpos)+1]

    @staticmethod
    def flatkeys(d, sep="/"):
        """
        Flatten a dictionary: build a new dictionary from a given one where all
        non-dict values are left untouched but nested ``dict``s are recursively
        merged in the new one with their keys prefixed by their parent key.

        >>> flatkeys({1: 42, 'foo': 12})
        {1: 42, 'foo': 12}
        >>> flatkeys({1: 42, 'foo': 12, 'bar': {'qux': True}})
        {1: 42, 'foo': 12, 'bar.qux': True}
        >>> flatkeys({1: {2: {3: 4}}})
        {'1.2.3': 4}
        >>> flatkeys({1: {2: {3: 4}, 5: 6}})
        {'1.2.3': 4, '1.5': 6}

        v0.1.0 by bfontaine, MIT license
        """
        flat = {}
        dicts = [("", d)]

        while dicts:
            prefix, d = dicts.pop()
            for k, v in d.items():
                k_s = str(k)
                if isinstance(v, collections.Mapping):
                    dicts.append(("%s%s%s" % (prefix, k_s, sep), v))
                else:
                    k_ = prefix + k_s if prefix else k
                    flat[k_] = v
        return flat

    def _build_path(self, key='', prepend=None):
        '''Build full path of current key given the rootpath and optionally a prepend'''
        return (self.delimiter).join(filter(None, [prepend, self.rootpath, key]))

    def _build_metadata(self, fullkeys):
        '''Build metadata to make viewitem and other methods using item resolution faster.
        Provided a list of full keys, this method will build parent nodes to point all the way down to the leaves.
        Only for fastview and fastview2 modes.'''

        if self.fastview:
            for fullkey in fullkeys:
                if not fullkey.endswith(self.delimiter):
                    # Fastview mode: create additional entries for each parent at every depths of the current leaf
                    parents = self._get_all_parent_nodes(fullkey, self.delimiter)

                    # First parent stores the direct path to the leaf
                    # Then we recursively add the path to the nested parent in all super parents.
                    lastparent = fullkey
                    for parent in parents:
                        if parent in self.d:
                            # There is already a parent entry, we add to the set
                            self.d.__getitem__(parent).add(lastparent)
                        else:
                            # Else we create a set and add this child
                            self.d.__setitem__(parent, set([lastparent]))
                        lastparent = parent
        else:
            # No fastview: we still build a parent node with a counter, so we know the number of items below
            # Each node will contain an integer, the number of direct descendant nodes and/or leaves
            # Create an empty entry for the parent element, so that we can quickly know if there are children for this key
            # Format is d['item1/'], with the ending delimiter
            for fullkey in fullkeys:
                if not fullkey.endswith(self.delimiter):
                    # Get list of parent nodes
                    parents = self._get_all_parent_nodes(fullkey, self.delimiter)
                    # All nodes will store the total number of subelements at any level, this is because if we store only direct descendants only then x['a/b/d'] and x['a/b/e'] will produce x['a/'] == 1 and that is wrong because then we cannot know how to decrement x['a/'] and when we can delete it.
                    for parent in parents:
                        if parent in self.d:
                            self.d[parent] += 1
                        else:
                            self.d.__setitem__(parent, 1)

    def __getitem__(self, key):
        '''Get an item given the key. O(1) in any case: if the item is a leaf, direct access, else if it is a node, a new fdict will be returned with a different rootpath but sharing the same internal dict.'''
        # Node or leaf?
        if key in self.d: # Leaf: return the value (leaf direct access test is why we do `in self.d` and not `in self`)
            return self.d.__getitem__(key)
        else: # Node: return a new full fdict based on the old one but with a different rootpath to limit the results by default (this is the magic that allows compatibility with the syntax d['item1']['item2'])
            return self.__class__(d=self.d, rootpath=self._build_path(key), delimiter=self.delimiter, fastview=self.fastview, **self.kwargs)

    def __setitem__(self, key, value):
        '''Set an item given the key. Supports for direct setting of nested elements without prior dict(), eg, x['a/b/c'] = 1. O(1) to set the item. If fastview mode, O(m*l) because of metadata building where m is the number of parents of current leaf, and l the number of leaves (if provided a nested dict).'''
        # TODO: fastview mode can setitem buildmetadata in O(2*(l+m)) linear time instead of O(l*m) quadratic time by first walking nodes and leafs of input dict and finally just merge the nodes sets with self.d, so we walk each parent only once, instead of walking each leaf and then each parent of each leaf repetitively.
        # Build the fullkey
        fullkey = self._build_path(key)

        # Store the item
        if isinstance(value, dict):
            # if the value is a dict, flatten it recursively or drop if empty

            # First we need to delete the previous value if it was a singleton
            if fullkey in self.d:
                self.__delitem__(key)

            # Flatten dict and store its leaves
            if not value:
                # User supplied an empty dict, the user wants to create a subdict, but it is not necessary here since nested dict are supported by default, just need to assign nested values
                return
            else:
                # else not empty dict
                if isinstance(value, self.__class__):
                    # If it is the same class as this, we merge
                    self.update(self.__class__({key: value}))
                else:
                    # If this is just a normal dict, we flatten it and merge
                    d2 = self.flatkeys({self._build_path(prepend=key) : value}, sep=self.delimiter)
                    self.d.update(d2)
                    if self.fastview:
                        self._build_metadata(self._generickeys(d2))
        else:
            # if the value is not a dict, we consider it a singleton/leaf, and we just build the full key and store the value as is
            if self.fastview:
                # Fastview mode: can ensure no conflict with a nested dict by managing the metadata
                dirkey = fullkey+self.delimiter
                # This key was a nested dict
                if dirkey in self.d:
                    # If this key was a nested dict before, we need to delete it recursively (with all subelements) and also delete pointer from parent node
                    self.__delitem__(key)
                # This key did not exist before but a parent is a singleton
                parents = self._get_all_parent_nodes(fullkey)
                for parent in parents:
                    parentleaf = parent[:len(parent)-1]
                    if parentleaf in self.d:
                        self.__delitem__(parentleaf)
                # Then we can rebuild the metadata to point to this new leaf
                self._build_metadata([fullkey])
            # and finally add the singleton as a leaf
            self.d.__setitem__(fullkey, value)

    def __delitem__(self, key):
        '''Delete an item in the internal dict, O(1) for any leaf, O(n) for a nested dict'''
        fullkey = self._build_path(key)
        if fullkey in self.d:
            # Key is a leaf, we can directly delete it
            if self.fastview:
                # Remove current node from its parent node's set()
                parentnode = self._get_parent_node(fullkey, self.delimiter)
                if parentnode: # if the node is not 1st-level (because then the parent is the root, it's then a fdict, not a set)
                    self.d.__getitem__(parentnode).remove(fullkey)
                    if not self.d.__getitem__(parentnode):
                        # if the set is now empty, just delete the node (to signal that there is nothing below now)
                        self.__delitem__(parentnode)  # recursive delete because the node is referenced by its parent
            # Delete the item!
            return self.d.__delitem__(fullkey)
        else:
            # Else there is no direct match, but might be a nested dict, we have to walk through all the dict
            dirkey = fullkey+self.delimiter
            flagdel = False
            if self.fastview:
                # Fastview mode: use the fast recursive viewkeys(), which will access the supplied node and walk down through all nested elements to build the list of items to delete, without having to walk the whole dict (only the subelements pointed by the current key and the subsubelements of the subkeys etc.)
                # Note that we ovveride the rootpath of viewkeys, because if delitem is called on a nested element (eg, del x['a']['b']), then the rootpath is the parent, so we will walk through all parent elements when we need only to walk from the child (the current node key), so this is both an optimization and also bugfix (because else we get a different behaviour if we use del x['a/b'] and del x['a']['b'])
                keystodel = [k for k in self.viewkeys(fullpath=True, nodes=True, rootpath=fullkey)]
                # We can already delete the current node key
                if dirkey in self.d:
                    self.d.__delitem__(dirkey)
                    flagdel = True
                # Remove current node from its parent node's set()
                parentnode = self._get_parent_node(dirkey, self.delimiter)
                if parentnode: # if the node is not 1st-level (because then the parent is the root, it's then a fdict, not a set)
                    self.d.__getitem__(parentnode).remove(dirkey)
                    if not self.d.__getitem__(parentnode):
                        # if the set is now empty, just delete the node (to signal that there is nothing below now)
                        self.__delitem__(parentnode)  # recursive delete because the node is referenced by its parent
            else:
                # Walk through all items in the dict and delete the nodes or nested elements starting from the supplied node (if any)
                keystodel = [k for k in self._viewkeys() if k.startswith(dirkey)]  # TODO: try to optimize with a generator instead of a list, but with viewkeys the dict is changing at the same time so we get runtime error!

            # Delete all matched keys
            for k in keystodel:
                self.d.__delitem__(k)

            # Check if we deleted at least one key, else raise a KeyError exception
            if not keystodel and not flagdel:
                raise KeyError(key)
            else:
                return

    def __contains__(self, key):
        '''Check existence of a key (or subkey) in the dictionary. O(1) for any leaf, O(n) at worst for nested dicts (eg, 'a' in d with d['a/b'] defined)'''
        fullkey = self._build_path(key)
        if self.d.__contains__(fullkey):
            # Key is a singleton/leaf, there is a direct match
            return True
        else:
            dirkey = fullkey+self.delimiter
            if self.fastview:
                # Fastview mode: nodes are stored so we can directly check in O(1)
                return self.d.__contains__(dirkey)
            else:
                # Key might be a node, but we have to check all items
                for k in self.viewkeys():
                    if k.startswith(dirkey):
                        return True
                return False

    def viewkeys(self, fullpath=False, nodes=False, rootpath=None):
        if not rootpath:
            # Allow to override rootpath, particularly useful for delitem (which is always called from parent, so the rootpath is incorrect, overriding the rootpath allows to limit the search breadth)
            rootpath = self.rootpath

        if not rootpath:
            if self.fastview:
                for k in self._viewkeys():
                    if not k.endswith(self.delimiter) or nodes:
                        yield k
            else:
                for k in self._viewkeys():
                    yield k
        else:
            pattern = rootpath+self.delimiter
            lpattern = len(pattern) if not fullpath else 0 # return the shortened path or fullpath?
            if self.fastview:
                # Fastview mode
                if pattern in self.d:
                    children = set()
                    children.update(self.d.__getitem__(pattern).copy())
                    while children:
                        child = children.pop()
                        if child.endswith(self.delimiter):
                            # Node, append all the subchildren to the stack
                            children.update(self.d.__getitem__(child))
                            if nodes:
                                yield child[lpattern:]
                        else:
                            # Leaf, return the key and value
                            yield child[lpattern:]
            else:
                for k in (k[lpattern:] for k in self._viewkeys() if k.startswith(pattern)):
                    yield k

    def viewitems(self, fullpath=False, nodes=False, rootpath=None):
        if not rootpath:
            # Allow to override rootpath, particularly useful for delitem (which is always called from parent, so the rootpath is incorrect, overriding the rootpath allows to limit the search breadth)
            rootpath = self.rootpath

        if not rootpath:
            # Return all items (because no rootpath, so no filter)
            if self.fastview:
                # Fastview mode, filter out nodes (ie, keys ending with delimiter) to keep only leaves
                for k,v in self._viewitems():
                    if not k.endswith(self.delimiter) or nodes:
                        yield k,v
            else:
                # No fastview, just return the internal dict's items
                for k,v in self._viewitems():
                    yield k,v
        else:
            # Else with rootpath, filter items to keep only the ones below the rootpath level
            # Prepare the pattern (the rootpath + delimiter) to filter items keys
            pattern = rootpath+self.delimiter
            lpattern = len(pattern) if not fullpath else 0 # return the shortened path or fullpath?
            if self.fastview:
                # Fastview mode, get the list of items directly from the current entry, and walk recursively all children to get down to the leaves
                if pattern in self.d:
                    children = set()
                    children.update(self.d.__getitem__(pattern))
                    while children:
                        child = children.pop()
                        if child.endswith(self.delimiter):
                            # Node, append all the subchildren to the stack
                            children.update(self.d.__getitem__(child))
                            if nodes:
                                yield child[lpattern:], self.d.__getitem__(child)
                        else:
                            # Leaf, return the key and value
                            yield child[lpattern:], self.d.__getitem__(child)
            else:
                # No fastview, just walk through all items and filter out the ones that are not in the current rootpath
                for k,v in ((k[lpattern:], v) for k,v in self._viewitems() if k.startswith(pattern)):
                    yield k,v

    def viewvalues(self, nodes=False, rootpath=None):
        if not rootpath:
            # Allow to override rootpath, particularly useful for delitem (which is always called from parent, so the rootpath is incorrect, overriding the rootpath allows to limit the search breadth)
            rootpath = self.rootpath

        if not rootpath:
            if self.fastview:
                for k,v in self._viewitems():
                    if not k.endswith(self.delimiter) or nodes:
                        yield v
            else:
                for v in self._viewvalues():
                    yield v
        else:
            pattern = rootpath+self.delimiter
            if self.fastview:
                # Fastview mode
                if pattern in self.d:
                    children = set()
                    children.update(self.d.__getitem__(pattern))
                    while children:
                        child = children.pop()
                        if child.endswith(self.delimiter):
                            # Node, append all the subchildren to the stack
                            children.update(self.d.__getitem__(child))
                            if nodes:
                                yield self.d.__getitem__(child)
                        else:
                            # Leaf, return the key and value
                            yield self.d.__getitem__(child)
            else:
                for v in (v for k,v in self._viewitems() if k.startswith(pattern)):
                    yield v

    iterkeys = viewkeys
    itervalues = viewvalues
    iteritems = viewitems
    if PY3:
        keys = viewkeys
        values = viewvalues
        items = viewitems
    else:
        def keys(self, *args, **kwargs):
            return list(self.viewkeys(*args, **kwargs))
        def values(self, *args, **kwargs):
            return list(self.viewvalues(*args, **kwargs))
        def items(self, *args, **kwargs):
            return list(self.viewitems(*args, **kwargs))

    def update(self, d2):
        if isinstance(d2, self.__class__):
            # Same class, we walk d2 but we cut d2 rootpath (fullpath=False) since we will rebase on our own self.d dict
            d2items = d2.viewitems(fullpath=False, nodes=False)  # ensure we do not add nodes, we need to rebuild anyway
        elif isinstance(d2, dict):
            # normal dict supplied
            d2 = self.flatkeys(d2, sep=self.delimiter) # first, flatten the dict keys
            d2items = self._genericitems(d2)
        else:
            raise ValueError('Supplied argument is not a dict.')

        # Update our dict with d2 leaves
        if self.rootpath:
            # There is a rootpath, so user is selecting a sub dict (eg, d['item1']), so we need to reconstruct d2 with the full key path rebased on self.d before merging
            rtncode = self.d.update((self._build_path(k), v) for k,v in d2items)
        else:
            # No rootpath, we can update directly because both dicts are comparable
            if isinstance(d2, self.__class__):
                rtncode = self.d.update(d2items)
            else:
                rtncode = self.d.update(d2)

        # Fastview mode: we have to take care of nodes, since they are set(), they will get replaced and we might lose some pointers as they will all be replaced by d2's pointers, so we have to merge them separately
        # The only solution is to skip d2 nodes altogether and rebuild the metadata for each new leaf added. This is faster than trying to merge separately each d2 set with self.d, because anyway we also have to rebuild for d2 root nodes (which might not be self.d root nodes particularly if rootpath is set)
        if self.fastview:
            self._build_metadata((self._build_path(k), v) for k,v in d2items)

        return rtncode

    def copy(self):
        fcopy = self.__class__(d=self.d.copy(), rootpath=self.rootpath, delimiter=self.delimiter, fastview=self.fastview, **self.kwargs)
        if self.fastview:
            # Fastview mode: we need to ensure we have copies of every sets used for nodes, else the nodes will reference (delitem included) the same items in both the original and the copied fdict!
            for k in fcopy._viewkeys():
                if k.endswith(fcopy.delimiter):
                    fcopy.d[k] = fcopy.d[k].copy()
        return fcopy

    @staticmethod
    def _count_iter_items(iterable):
        '''
        Consume an iterable not reading it into memory; return the number of items.
        by zuo: https://stackoverflow.com/a/15112059/1121352
        '''
        counter = itertools.count()
        collections.deque(_zip(iterable, counter), maxlen=0)  # (consume at C speed)
        return next(counter)

    def __len__(self):
        if not self.rootpath:
            return self.d.__len__()
        else:
            # If there is a rootpath, we have to limit the length to the subelements
            return self._count_iter_items(self.viewkeys())

    def __eq__(self, d2):
        is_d2fdict = isinstance(d2, self.__class__)
        if is_d2fdict and not self.rootpath:
            # fdict, we can directly compare
            return (self.d == d2.d)
        else:
            kwargs = {}
            if is_d2fdict:
                if len(self) != len(d2):
                    # If size is different then the dicts are different
                    # Note that we need to compare the items because we need to filter if we are looking at nested keys (ie, if there is a rootpath)
                    return False
                else:
                    kwargs['fullpath'] = False
            elif isinstance(d2, dict): # normal dict, need to flatten it first
                d2 = fdict.flatkeys(d2, sep=self.delimiter)
                if len(self) != len(d2):
                    return False

            # Else size is the same, check each item if they are equal
            # TOREMOVE COMMENT: There is a rootpath, this is a subdict, so we have to filter the items we compare (else we will compare the full dict to d2, which is probably not what the user wants if he does d['item1'] == d2)
            if PY3:
                d2items = d2.items(**kwargs)
            else:
                d2items = d2.viewitems(**kwargs)
            for k, v in d2items:
                fullkey = self._build_path(k)
                if not fullkey in self.d or self.d.__getitem__(fullkey) != v:
                    return False
            return True

    def __repr__(self):
        # Filter the items if there is a rootpath and return as a new fdict
        if self.rootpath:
            return repr(self.__class__(d=dict(self.items()), rootpath='', delimiter=self.delimiter, fastview=self.fastview, **self.kwargs))
        else:
            try:
                return self.d.__repr__()
            except AttributeError as exc:
                return repr(dict(self.items()))

    def __str__(self):
        if self.rootpath:
            return str(self.__class__(d=dict(self.items()), rootpath='', delimiter=self.delimiter, fastview=self.fastview, **self.kwargs))
        else:
            try:
                return self.d.__str__()
            except AttributeError as exc:
                return str(dict(self.items()))

    def to_dict(self):
        '''Convert to a flattened dict'''
        return dict(self.items())

    def extract(self, fullpath=True):
        '''Return a new fdict shortened to only the currently subselected items, but instead of fdict, should also support sfdict or any child class
        It was chosen to return a fdict still containing the full keys and not the shortened ones because else it becomes very difficult to merge fdicts
        And also for subdicts (like sfdict) which might store in a file, so we don't want to start mixing up different paths in the same file, but we would like to extract to a fdict with same parameters as the original, so keeping full path is the only way to do so coherently.
        '''
        if fullpath:
            return self.__class__(d=self.items(fullpath=True), rootpath=self.rootpath, delimiter=self.delimiter, fastview=self.fastview, **self.kwargs)
        else:
            return self.__class__(d=self.items(fullpath=False), rootpath='', delimiter=self.delimiter, fastview=self.fastview) # , **self.kwargs)  # if not fullpath for keys, then we do not propagate kwargs because it might implicate propagating filename saving and mixing up keys. For fdict, this does not make a difference, but it might for subclassed dicts. Override this function if you want to ensure that an extract has all same parameters as original when fullpath=False in your subclassed dict.

    def to_dict_nested(self):
        '''Convert to a nested dict'''
        d2 = {}
        delimiter = self.delimiter
        # Constuct the nested dict for each leaf
        for k, v in self.viewitems(nodes=False):
            # Get all parents of the current leaf, from root down to the leaf's direct parent
            parents = self._get_all_parent_nodes_nested(k, delimiter)
            # Recursively create each node of this subdict branch
            d2sub = d2
            for parent in parents:
                if not parent in d2sub:
                    # Create the node if it does not exist
                    d2sub[parent] = {}
                # Continue from this node
                d2sub = d2sub[parent]
            # get leaf key
            k = k[k.rfind(delimiter)+1:]
            # set leaf value
            d2sub[k] = v
        return d2


class sfdict(fdict):
    '''A nested dict with flattened internal representation, combined with shelve to allow for efficient storage and memory allocation of huge nested dictionnaries.
    If you change leaf items (eg, list.append), do not forget to sync() to commit changes to disk and empty memory cache because else this class has no way to know if leaf items were changed!
    '''
    def __init__(self, *args, **kwargs):
        # Initialize specific arguments for sfdict
        if not ('filename' in kwargs):
            self.filename = tempfile.NamedTemporaryFile(delete=False).name
        else:
            self.filename = kwargs['filename']
            #del kwargs['filename'] # do not del for auto management of internal sub calls to sfdict

        if 'autosync' in kwargs:
            self.autosync = kwargs['autosync']
            #del kwargs['autosync']
        else:
            self.autosync = True

        # Initialize parent class
        fdict.__init__(self, *args, **kwargs)

        # Replace internal dict with an out-of-core shelve
        self.d = shelve.open(filename=self.filename, flag='c', writeback=True)

        # Call compatibility layer
        self._viewkeys, self._viewvalues, self._viewitems = self._getitermethods(self.d)

    def __setitem__(self, key, value):
        fdict.__setitem__(self, key, value)
        if self.autosync:
            self.sync()

    def get_filename(self):
        if self.filename:
            return self.filename
        else:
            return self.d.dict._datfile

    def sync(self):
        self.d.sync()

    def close(self):
        self.d.close()