
from functools import wraps

from docutils.core import publish_doctree

from rinoh.text import MixedStyledText
from rinoh.flowable import StaticGroupedFlowables
from rinoh.util import all_subclasses


class CustomElement(object):
    @classmethod
    def map_node(cls, node):
        return cls.MAPPING[node.__class__.__name__](node)

    def __init__(self, doctree_node):
        self.node = doctree_node

    def __getattr__(self, name):
        for child in self.node.children:
            if child.tagname == name:
                return self.map_node(child)
        raise AttributeError('No such element: {}'.format(name))

    def __getitem__(self, name):
        return self.node[name]

    def __iter__(self):
        try:
            for child in self.parent.node.children:
                if child.tagname == self.node.tagname:
                    yield self.map_node(child)
        except AttributeError:
            # this is the root element
            yield self

    @property
    def parent(self):
        if self.node.parent is not None:
            return self.map_node(self.node.parent)

    @property
    def text(self):
        return self.node.astext()

    def get(self, key, default=None):
        return self.node.get(key, default)

    def getchildren(self):
        return [self.map_node(child) for child in self.node.children]

    def process_content(self):
        return MixedStyledText([text
                                for text in (child.styled_text()
                                             for child in self.getchildren())
                                if text])

    @property
    def location(self):
        return '{}: <{}> at line {}'.format(self.node.source,
                                            self.node.tagname,
                                            self.node.line)


def set_source(method):
    """Decorator that sets the `source` attribute of the returned object to
    `self`"""
    @wraps(method)
    def method_wrapper(obj, *args, **kwargs):
        result = method(obj, *args, **kwargs)
        try:
            result.source = obj
        except AttributeError:
            pass
        return result
    return method_wrapper


class BodyElement(CustomElement):
    @set_source
    def flowable(self):
        return self.build_flowable()

    def build_flowable(self):
        raise NotImplementedError('tag: %s' % self.tag)


class BodySubElement(CustomElement):
    def process(self):
        raise NotImplementedError('tag: %s' % self.tag)


class InlineElement(CustomElement):
    @set_source
    def styled_text(self):
        return self.build_styled_text()

    def build_styled_text(self):
        raise NotImplementedError('tag: %s' % self.tag)


class GroupingElement(BodyElement):
    style = None

    def build_flowable(self):
        return StaticGroupedFlowables([item.flowable()
                                       for item in self.getchildren()],
                                      style=self.style)


from . import nodes

CustomElement.MAPPING = {cls.__name__.lower(): cls
                         for cls in all_subclasses(CustomElement)}
CustomElement.MAPPING['Text'] = nodes.Text


class ReStructuredTextParser(object):
    def parse(self, filename):
        with open(filename) as file:
            doctree = publish_doctree(file.read(), source_path=filename)
        return CustomElement.map_node(doctree.document)
