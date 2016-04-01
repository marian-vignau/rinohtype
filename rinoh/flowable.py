# This file is part of RinohType, the Python document preparation system.
#
# Copyright (c) Brecht Machiels.
#
# Use of this source code is subject to the terms of the GNU Affero General
# Public License v3. See the LICENSE file or http://www.gnu.org/licenses/.

"""
Base classes for flowable and floating document elements. These are elements
that make up the content of a document and are rendered onto its pages.

* :class:`Flowable`: Element that is rendered onto a :class:`Container`.
* :class:`FlowableStyle`: Style class specifying the vertical space surrounding
                          a :class:`Flowable`.
* :class:`Floating`: Decorator to transform a :class:`Flowable` into a floating
                     element.
"""


from copy import copy
from itertools import chain, tee

from .dimension import DimensionBase, PT
from .draw import ShapeStyle, Rectangle, Line, LineStyle
from .layout import (InlineDownExpandingContainer, VirtualContainer,
                     MaybeContainer, discard_state, ContainerOverflow,
                     EndOfContainer, PageBreakException)
from .style import Styled, OptionSet, Attribute, OverrideDefault, Bool
from .text import StyledText
from .util import ReadAliasAttribute, NotImplementedAttribute


__all__ = ['Flowable', 'FlowableStyle',
           'DummyFlowable', 'WarnFlowable', 'SetMetadataFlowable',
           'AddToFrontMatter',
           'InseparableFlowables', 'GroupedFlowables', 'StaticGroupedFlowables',
           'LabeledFlowable', 'GroupedLabeledFlowables',
           'HorizontallyAlignedFlowable', 'HorizontallyAlignedFlowableStyle',
           'Float',
           'PageBreak', 'PageBreakStyle']


class FlowableStyle(ShapeStyle):
    """The :class:`Style` for :class:`Flowable` objects."""

    space_above = Attribute(DimensionBase, 0, 'Vertical space preceding the '
                                              'flowable')
    space_below = Attribute(DimensionBase, 0, 'Vertical space following the '
                                              'flowable')
    margin_left = Attribute(DimensionBase, 0, 'Left margin')
    margin_right = Attribute(DimensionBase, 0, 'Right margin')
    padding_left = Attribute(DimensionBase, 0, 'Left padding')
    padding_right = Attribute(DimensionBase, 0, 'Right padding')
    padding_top = Attribute(DimensionBase, 0, 'Top padding')
    padding_bottom = Attribute(DimensionBase, 0, 'Bottom padding')
    keep_with_next = Attribute(Bool, False, 'Keep this flowable and the next '
                                            'on the same page')
    stroke_color = OverrideDefault(None)
    fill_color = OverrideDefault(None)

    default_base = None


class FlowableState(object):
    """Stores a :class:`Flowable`\'s rendering state, which can be copied. This
    enables saving the rendering state at certain points in the rendering
    process, so rendering can later be resumed at those points, if needed."""

    def __init__(self, flowable, _initial=True):
        self.flowable = flowable
        self.initial = _initial

    def __copy__(self):
        return self.__class__(self.flowable, _initial=self.initial)


class Flowable(Styled):
    """An element that can be 'flowed' into a :class:`Container`. A flowable can
    adapt to the width of the container, or it can horizontally align itself in
    the container."""

    style_class = FlowableStyle

    def __init__(self, id=None, style=None, parent=None):
        """Initialize this flowable and associate it with the given `style` and
        `parent` (see :class:`Styled`)."""
        super().__init__(id=id, style=style, parent=parent)

    @property
    def level(self):
        try:
            return self.parent.level
        except AttributeError:
            return 0

    @property
    def section(self):
        try:
            return self.parent.section
        except AttributeError:
            return None

    def initial_state(self, container):
        return FlowableState(self)

    def flow(self, container, last_descender, state=None, **kwargs):
        """Flow this flowable into `container` and return the vertical space
        consumed.

        The flowable's contents is preceded by a vertical space with a height
        as specified in its style's `space_above` attribute. Similarly, the
        flowed content is followed by a vertical space with a height given
        by the `space_below` style attribute."""
        top_to_baseline = 0
        state = state or self.initial_state(container)
        if state.initial:
            space_above = self.get_style('space_above', container)
            try:
                container.advance(float(space_above))
            except ContainerOverflow:
                raise EndOfContainer(state)
            top_to_baseline += float(space_above)
        margin_left = self.get_style('margin_left', container)
        margin_right = self.get_style('margin_right', container)
        right = container.width - margin_right
        with InlineDownExpandingContainer('MARGIN', container, left=margin_left,
                                          right=right) as margin_container:
            initial_before, initial_after = state.initial, True
            try:
                width, inner_top_to_baseline, descender = \
                    self.flow_inner(margin_container, last_descender,
                                    state=state, **kwargs)
                top_to_baseline += inner_top_to_baseline
                initial_after = False
            except EndOfContainer as eoc:
                initial_after = eoc.flowable_state.initial
                raise eoc
            finally:
                reference_id = self.get_id(container.document, create=False)
                if initial_before and not initial_after:
                    container.flowed_flowables.append(self)
                    if reference_id:
                        self.create_destination(margin_container, True)
        container.advance(float(self.get_style('space_below', container)), True)
        return margin_left + width + margin_right, top_to_baseline, descender

    def flow_inner(self, container, descender, state=None, **kwargs):
        draw_top = state.initial
        padding_top = self.get_style('padding_top', container)
        padding_left = self.get_style('padding_left', container)
        padding_right = self.get_style('padding_right', container)
        padding_bottom = float(self.get_style('padding_bottom', container))
        pad_kwargs = dict(left=padding_left,
                          right=container.width - padding_right,
                          extra_space_below=padding_bottom)
        try:
            container.advance(padding_top)
        except ContainerOverflow:
            raise EndOfContainer(state)
        try:
            with InlineDownExpandingContainer('PADDING', container,
                                              **pad_kwargs) as pad_cntnr:
                width, first_line_ascender, descender = \
                    self.render(pad_cntnr, descender, state=state, **kwargs)
            self.render_frame(container, container.height, top=draw_top)
            top_to_baseline = padding_top + first_line_ascender
            return width, top_to_baseline, descender
        except EndOfContainer as eoc:
            if not eoc.flowable_state.initial:
                self.render_frame(container, container.max_height,
                                  top=draw_top, bottom=False)
            raise

    def render_frame(self, container, container_height, top=True, bottom=True):
        width, height = float(container.width), - float(container_height)
        stroke_width = self.get_style('stroke_width', container)
        stroke_color = self.get_style('stroke_color', container)
        fill_color = self.get_style('fill_color', container)
        fill_style = ShapeStyle(stroke_color=None, fill_color=fill_color)
        rect = Rectangle((0, 0), width, height, style=fill_style, parent=self)
        rect.render(container)
        style = dict(style=LineStyle(stroke_width=stroke_width,
                                     stroke_color=stroke_color))
        if top:
            Line((0, 0), (width, 0), **style).render(container)
        Line((0, 0), (0, height), **style).render(container)          # left
        Line((width, 0), (width, height), **style).render(container)  # right
        if bottom:
            Line((0, height), (width, height), **style).render(container)

    def render(self, container, descender, state):
        """Renders the flowable's content to `container`, with the flowable's
        top edge lining up with the container's cursor. `descender` is the
        descender height of the preceding line or `None`."""
        raise NotImplementedError

    def after_rendering(self, container):
        pass


# flowables that do not render anything (but with optional side-effects)

class DummyFlowable(Flowable):
    style_class = None

    def __init__(self, parent=None):
        super().__init__(parent=parent)

    def get_style(self, attribute, flowable_target):
        if attribute == 'keep_with_next':
            return False
        raise TypeError

    def flow(self, container, last_descender, state=None):
        return 0, 0, last_descender


class WarnFlowable(DummyFlowable):
    def __init__(self, message, parent=None):
        super().__init__(parent=parent)
        self.message = message

    def flow(self, container, last_descender, state=None):
        self.warn(self.message, container)
        return super().flow(container, last_descender, state)


class SetMetadataFlowable(DummyFlowable):
    def __init__(self, parent=None, **metadata):
        super().__init__(parent=parent)
        self.metadata = metadata

    def build_document(self, document):
        document.metadata.update(self.metadata)


class AddToFrontMatter(DummyFlowable):
    def __init__(self, flowables, parent=None):
        super().__init__(parent=parent)
        self.flowables = flowables

    def build_document(self, document):
        document.front_matter.append(self.flowables)


# grouping flowables

class InseparableFlowables(Flowable):
    def render(self, container, last_descender, state):
        max_flowable_width = 0
        first_top_to_baseline = None
        with MaybeContainer(container) as maybe_container, \
                discard_state(state):
            for flowable in self.flowables(container.document):
                width, top_to_baseline, last_descender = \
                    flowable.flow(maybe_container, last_descender)
                max_flowable_width = max(max_flowable_width, width)
                if first_top_to_baseline is None:
                    first_top_to_baseline = top_to_baseline
        return max_flowable_width, first_top_to_baseline or 0, last_descender


class GroupedFlowablesState(FlowableState):
    def __init__(self, groupedflowables, flowables, first_flowable_state=None,
                 _initial=True):
        super().__init__(groupedflowables, _initial)
        self.flowables = flowables
        self.first_flowable_state = first_flowable_state

    groupedflowables = ReadAliasAttribute('flowable')

    def __copy__(self):
        copy_flowables, self.flowables = tee(self.flowables)
        copy_first_flowable_state = copy(self.first_flowable_state)
        return self.__class__(self.groupedflowables, copy_flowables,
                              copy_first_flowable_state, _initial=self.initial)

    def next_flowable(self):
        return next(self.flowables)

    def prepend(self, first_flowable_state):
        first_flowable = first_flowable_state.flowable
        self.flowables = chain((first_flowable, ), self.flowables)
        if first_flowable_state:
            self.first_flowable_state = first_flowable_state
            self.initial = self.initial and first_flowable_state.initial


class GroupedFlowablesStyle(FlowableStyle):
    title = Attribute(StyledText, None, 'Title to precede the flowables')
    flowable_spacing = Attribute(DimensionBase, 0, 'Spacing between flowables')


class GroupedFlowables(Flowable):
    style_class = GroupedFlowablesStyle

    def flowables(self, container):
        raise NotImplementedError

    def initial_state(self, container):
        flowables_iter = self.flowables(container)
        title_text = self.get_style('title', container)
        if title_text:
            title = Paragraph(title_text, style='title')
            flowables_iter = chain((title, ), flowables_iter)
        return GroupedFlowablesState(self, flowables_iter)

    def render(self, container, descender, state, **kwargs):
        max_flowable_width = 0
        first_top_to_baseline = None
        item_spacing = self.get_style('flowable_spacing', container)
        try:
            saved_state = copy(state)
            while True:
                width, top_to_baseline, descender = \
                    self._flow_with_next(state, container, descender, **kwargs)
                if first_top_to_baseline is None:
                    first_top_to_baseline = top_to_baseline
                max_flowable_width = max(max_flowable_width, width)
                saved_state = copy(state)
                container.advance(item_spacing, True)
        except KeepWithNextException:
            raise EndOfContainer(saved_state)
        except EndOfContainer as eoc:
            state.prepend(eoc.flowable_state)
            raise EndOfContainer(state, eoc.page_break)
        except StopIteration:
            return max_flowable_width, first_top_to_baseline or 0, descender

    def _flow_with_next(self, state, container, descender, **kwargs):
        flowable = state.next_flowable()
        flowable.parent = self
        with MaybeContainer(container) as maybe_container:
            width, top_to_baseline, descender = \
                flowable.flow(maybe_container, descender,
                              state=state.first_flowable_state, **kwargs)
        state.initial = False
        state.first_flowable_state = None
        if flowable.get_style('keep_with_next', container):
            item_spacing = self.get_style('flowable_spacing', container)
            maybe_container.advance(item_spacing)
            try:
                width, _, descender = self._flow_with_next(state, container,
                                                           descender, **kwargs)
            except EndOfContainer as eoc:
                if eoc.flowable_state.initial:
                    maybe_container.do_place(False)
                    raise KeepWithNextException
                else:
                    raise
        return width, top_to_baseline, descender


class KeepWithNextException(Exception):
    pass


class StaticGroupedFlowables(GroupedFlowables):
    def __init__(self, flowables, id=None, style=None, parent=None):
        super().__init__(id=id, style=style, parent=parent)
        self.children = []
        for flowable in flowables:
            self.append(flowable)

    def append(self, flowable):
        flowable.parent = self
        self.children.append(flowable)

    def flowables(self, container):
        return iter(self.children)

    def build_document(self, document):
        super().build_document(document)
        for flowable in self.flowables(document):
            flowable.build_document(document)

    def prepare(self, flowable_target):
        super().prepare(flowable_target)
        for flowable in self.flowables(flowable_target.document):
            flowable.parent = self
            flowable.prepare(flowable_target)


class LabeledFlowableStyle(FlowableStyle):
    label_min_width = Attribute(DimensionBase, 12*PT, 'Minimum label width')
    label_max_width = Attribute(DimensionBase, 80*PT, 'Maximum label width')
    label_spacing = Attribute(DimensionBase, 3*PT, 'Spacing between a label and'
                                                   'the labeled flowable')
    wrap_label = Attribute(bool, False, 'Wrap the label at `label_max_width`')


class LabeledFlowableState(FlowableState):
    def __init__(self, flowable, content_flowable_state, _initial=True):
        super().__init__(flowable, _initial=_initial)
        self.content_flowable_state = content_flowable_state

    def update(self):
        self.initial = self.initial and self.content_flowable_state.initial

    def __copy__(self):
        return self.__class__(self.flowable, copy(self.content_flowable_state),
                              _initial=self.initial)


class LabeledFlowable(Flowable):
    style_class = LabeledFlowableStyle

    def __init__(self, label, flowable, id=None, style=None, parent=None):
        super().__init__(id=id, style=style, parent=parent)
        self.label = label
        self.flowable = flowable
        label.parent = flowable.parent = self

    def prepare(self, flowable_target):
        super().prepare(flowable_target)
        self.label.prepare(flowable_target)
        self.flowable.prepare(flowable_target)

    def label_width(self, container):
        virtual_container = VirtualContainer(container)
        label_width, _, _ = self.label.flow(virtual_container, 0)
        return label_width

    def initial_state(self, container):
        initial_content_state = self.flowable.initial_state(container)
        return LabeledFlowableState(self, initial_content_state)

    def render(self, container, last_descender, state, max_label_width=None):
        label_column_min_width = self.get_style('label_min_width', container)
        label_column_max_width = self.get_style('label_max_width', container)
        label_spacing = self.get_style('label_spacing', container)
        wrap_label = self.get_style('wrap_label', container)

        label_width = self.label_width(container)
        max_label_width = max_label_width or label_width
        label_column_width = max(label_column_min_width,
                                 min(max_label_width, label_column_max_width))
        left = label_column_width + label_spacing
        label_spillover = not wrap_label and label_width > label_column_width

        def render_label(container, baseline_offset_label=0):
            width = None if label_spillover else label_column_width
            with InlineDownExpandingContainer('LABEL', container, width=width,
                    advance_parent=False) as label_container:
                label_container.advance(baseline_offset_label)
                _, top_to_baseline, descender = \
                    self.label.flow(label_container, last_descender)
            return label_container.cursor, top_to_baseline, descender

        def render_content(container, descender, state):
            with InlineDownExpandingContainer('CONTENT', container, left=left,
                    advance_parent=False) as content_container:
                width, top_to_baseline, descender = \
                    self.flowable.flow(content_container, descender,
                                       state=state)
            return width, content_container.cursor, top_to_baseline, descender

        if not label_spillover:
            try:
                with MaybeContainer(container) as maybe_container:
                    _, label_top_to_baseline, _ = render_label(maybe_container)
            except EndOfContainer:
                label_top_to_baseline = 0
            maybe_container._do_place = False
            copy_of_content_state = copy(state.content_flowable_state)
            try:
                with MaybeContainer(container) as maybe_container:
                    _, _, content_top_to_baseline, _ = \
                        render_content(maybe_container, last_descender,
                                       copy_of_content_state)
            except EndOfContainer:
                content_top_to_baseline = 0
            maybe_container._do_place = False
        top_to_baseline = max(label_top_to_baseline, content_top_to_baseline)
        offset_label = top_to_baseline - label_top_to_baseline
        offset_content = top_to_baseline - content_top_to_baseline

        try:
            with MaybeContainer(container) as maybe_container:
                if state.initial:
                    label_height, _, label_desc = render_label(maybe_container,
                                                               offset_label)
                    if label_spillover:
                        maybe_container.advance(label_height)
                        last_descender = label_desc
                else:
                    label_height = label_desc = 0
                maybe_container.advance(offset_content)
                width, content_height, _, content_desc = \
                    render_content(maybe_container, last_descender,
                                   state.content_flowable_state)
        except (ContainerOverflow, EndOfContainer):
            state.update()
            raise EndOfContainer(state)
        if label_spillover:
            container.advance(content_height)
            descender = content_desc
        else:
            if content_height > label_height:
                container.advance(content_height)
                descender = content_desc
            else:
                container.advance(label_height)
                descender = label_desc
        return left + width, label_top_to_baseline, descender


class GroupedLabeledFlowables(GroupedFlowables):
    def _calculate_label_width(self, container):
        return max(flowable.label_width(container)
                   for flowable in self.flowables(container))

    def render(self, container, descender, state):
        if state.initial:
            max_label_width = self._calculate_label_width(container)
        else:
            max_label_width = state.max_label_width
        try:
            return super().render(container, descender, state=state,
                                  max_label_width=max_label_width)
        except EndOfContainer as eoc:
            eoc.flowable_state.max_label_width = max_label_width
            raise


LEFT = 'left'
RIGHT = 'right'
CENTER = 'center'


class HorizontalAlignment(OptionSet):
    values = LEFT, RIGHT, CENTER


class HorizontallyAlignedFlowableStyle(FlowableStyle):
    horizontal_align = Attribute(HorizontalAlignment, LEFT,
                                 'Horizontal alignment of the flowable')


class HorizontallyAlignedFlowableState(FlowableState):
    width = NotImplementedAttribute()


class HorizontallyAlignedFlowable(Flowable):
    style_class = HorizontallyAlignedFlowableStyle

    def __init__(self, *args, align=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.align = align

    def _align(self, container, width):
        align = self.align or self.get_style('horizontal_align', container)
        if align == LEFT or width is None:
            return
        left_extra = float(container.width - width)
        if align == CENTER:
            left_extra /= 2
        container.left = float(container.left) + left_extra

    def flow(self, container, last_descender, state=None):
        with MaybeContainer(container) as align_container:
            try:
                width, top_to_baseline, descender = \
                    super().flow(align_container, last_descender, state)
            except EndOfContainer as eoc:
                width = eoc.flowable_state.width
                raise
            finally:
                self._align(align_container, width)
        return container.width, top_to_baseline, descender


class Float(Flowable):
    """Transform a :class:`Flowable` into a floating element. A floating element
    or 'float' is not flowed into its designated container, but is forwarded to
    another container pointed to by the former's :attr:`Container.float_space`
    attribute.

    This is typically used to place figures and tables at the top or bottom of a
    page, instead of in between paragraphs."""

    def __init__(self, flowable, style=None, parent=None):
        super().__init__(style=style, parent=parent)
        self.flowable = flowable
        flowable.parent = self

    def prepare(self, flowable_target):
        self.flowable.prepare(flowable_target)

    def flow(self, container, last_descender, state=None):
        """Flow contents into the float space associated with `container`."""
        if self not in container.document.floats:
            self.flowable.flow(container.float_space, None)
            container.document.floats.add(self)
            container.page.check_overflow()
        return 0, 0, last_descender


ANY = 'any'


class Break(OptionSet):
    values = None, ANY, LEFT, RIGHT


class PageBreakStyle(FlowableStyle):
    page_break = Attribute(Break, None, 'Type of page break to insert '
                                        'before rendering this flowable')


class PageBreak(Flowable):
    style_class = PageBreakStyle
    exception_class = PageBreakException

    def flow(self, container, last_descender, state=None):
        this_page_type = LEFT if container.page.number % 2 == 0 else RIGHT
        page_break = self.get_style('page_break', container)
        if not state and page_break:
            if not (container.page._empty
                    and page_break in (ANY, this_page_type)):
                if page_break == ANY:
                    page_break = LEFT if container.page.number % 2 else RIGHT
                chain = container.chained_ancestor.chain
                raise self.exception_class(page_break, chain)
        return super().flow(container, last_descender, state)

    def render(self, container, descender, state):
        return 0, 0, descender


from .paragraph import Paragraph
