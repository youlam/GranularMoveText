import sublime_plugin
import sublime
from sublime import Region


def generic_line_regions_from_pt(view, pt):
    line = view.line(pt)
    line_string = view.substr(line)

    i = 0
    while i < len(line_string) and line_string[i] == ' ':
        i += 1

    if i < len(line_string):
        j = 0
        while j < len(line_string) and line_string[-1 - j] == ' ':
            j += 1
        assert i + j < len(line_string)
        assert line.begin() + i < line.end() - j
        source = Region(line.begin() + i, line.end() - j)

    else:
        j = 0
        source = None

    return line, source


def move_pt_via_sublime(view, pt, by, forward):
    assert by in ['char', 'subword', 'word', 'bigword']

    if by == 'char':
        return min(view.size(), pt + 1) if forward else max(0, pt - 1)

    if by == 'bigword':
        by = 'word'

    assert by in ['subword', 'word']

    by = by + "_ends" if forward else by + "s"

    regions = view.sel()
    regions_copy = list(regions)
    regions.clear()
    regions.add(Region(pt))

    view.run_command("move", {"forward": forward, "by": by})

    to_return = regions[0].a
    regions.clear()
    regions.add_all(regions_copy)

    return to_return


class CutSelection():
    def __init__(self, view, edit, r, by):
        self.view = view
        self.edit = edit
        self.by = by

        assert self.by in ['line', 'char', 'subword', 'word', 'bigword', 'eol', 'bol']

        self.horizontal = self.by != 'line'
        self.vertical = not self.horizontal

        if r.size() == 0 and self.vertical:
            self.starter_region = view.full_line(r.a)
            self.caret_within_line = r.a - self.starter_region.begin()

        else:
            self.starter_region = r
            self.caret_within_line = None

        self.string = view.substr(self.starter_region)

        if not self.horizontal:
            xpos_unit = view.text_to_layout(1)[0]
            assert xpos_unit != 0

            if r.xpos >= 0 and not self.caret_within_line:
                self.desired_xpos = r.xpos
                self.desired_column = (r.xpos / xpos_unit)

            else:
                self.desired_column = view.rowcol(self.starter_region.begin())[1]
                self.desired_xpos = self.desired_column * xpos_unit

            print("self.desired_column:", self.desired_column)

        self.row = self.pt = None

    def commit_erasure(self):
        assert self.pt is None and self.row is None
        to_return = self.starter_region
        self.view.erase(self.edit, self.starter_region)
        if self.horizontal:
            self.pt = self.starter_region.begin()
        else:
            self.row = self.view.rowcol(self.starter_region.begin())[0]
        self.starter_region = None
        return to_return

    def notify_of_erasure(self, r):
        assert self.pt is None and self.row is None and self.starter_region is not None
        assert r.end() <= self.starter_region.begin()
        self.starter_region = Region(self.starter_region.a - r.size(), self.starter_region.b - r.size())

    def midway_consistency(self):
        if self.horizontal:
            return self.pt is not None and self.row is None and self.starter_region is None
        return self.pt is None and self.row is not None and self.starter_region is None

    def move_vertical(self, forward, num_times=1):
        assert not self.horizontal
        assert self.midway_consistency()
        maxrow, __ = self.view.rowcol(self.view.size())
        if forward:
            self.row = min(maxrow, self.row + num_times)
        else:
            self.row = max(0, self.row - num_times)

    def move_horizontal(self, forward, num_times):
        assert self.horizontal
        assert self.midway_consistency()

        self.desired_column = None
        self.desired_xpos = None

        for i in range(num_times):
            if self.by in ['char', 'subword', 'word', 'bigword']:
                if len(sublime.find_resources("GranularSubword.py")) > 0:
                    from GranularSubword.GranularSubword import granular_move_pt
                    self.pt = granular_move_pt(self.view, self.pt, self.by, forward)

                else:
                    self.pt = move_pt_via_sublime(self.view, self.pt, self.by, forward)

            else:
                assert num_times == 1
                assert self.by in ['eol', 'bol']
                if self.by == 'eol':
                    self.pt = self.view.line(self.pt).end()

                else:
                    line, source = generic_line_regions_from_pt(self.view, self.pt)

                    if source and self.pt != source.begin():
                        self.pt = source.begin()

                    else:
                        self.pt = line.begin()

    def move(self, forward, num_times):
        if self.horizontal:
            self.move_horizontal(forward, num_times)

        else:
            self.move_vertical(forward, num_times)

    def commit_insertion(self):
        assert self.midway_consistency()

        if self.horizontal:
            self.view.insert(self.edit, self.pt, self.string)
            r = Region(self.pt, self.pt + len(self.string))
            self.view.sel().add(r)
            self.pt = None

        else:
            pt = self.view.text_point(self.row, 0)
            line = self.view.line(pt)

            col = min(line.size(), self.desired_column)
            pt = self.view.text_point(self.row, col)
            copy = list(self.view.sel())
            self.view.insert(self.edit, pt, self.string)
            # nuke and reload... (have to because the string insertion erases xpos values)
            self.view.sel().clear()
            self.view.sel().add_all(copy)
            r = Region(pt, pt + len(self.string), self.desired_xpos)
            if self.caret_within_line is not None:
                assert col == 0
                self.view.sel().add(Region(pt + self.caret_within_line))
            else:
                self.view.sel().add(r)
            self.row = None

    def notify_of_insertion(self, last_added_string):
        assert self.midway_consistency()
        if self.horizontal:
            self.pt += len(last_added_string)

        else:
            self.row += len([x for x in last_added_string if x == '\n'])


def regions_to_cut_selections(view, edit, by):
    regions = view.sel()
    initial_cuts = []
    cuts = []

    for r in regions:
        initial_cuts.append(CutSelection(view, edit, r, by))

    regions.clear()

    for z in initial_cuts:
        if len(cuts) == 0 or cuts[-1].starter_region.end() <= z.starter_region.begin():
            cuts.append(z)

    for index, z in enumerate(cuts):
        r = z.commit_erasure()
        for w in cuts[index + 1:]:
            w.notify_of_erasure(r)

    return cuts


def grab_text(view, edit, by, forward=True, num_times=1):
    assert by in ['line', 'char', 'subword', 'word', 'bigword', 'eol', 'bol']

    cut_selections = regions_to_cut_selections(view, edit, by)

    assert len(view.sel()) == 0

    for c in cut_selections:
        c.move(forward, num_times)

    for index, c in enumerate(cut_selections):
        c.commit_insertion()
        for q in cut_selections[index + 1:]:
            q.notify_of_insertion(c.string)


class GranularMoveTextUp(sublime_plugin.TextCommand):
    def run(self, edit, num_times=1):
        grab_text(self.view, edit, by="line", forward=False, num_times=num_times)


class GranularMoveTextDown(sublime_plugin.TextCommand):
    def run(self, edit, num_times=1):
        grab_text(self.view, edit, by="line", forward=True, num_times=num_times)


class GranularMoveTextLeft(sublime_plugin.TextCommand):
    def run(self, edit, by="char"):
        grab_text(self.view, edit, by=by, forward=False)


class GranularMoveTextRight(sublime_plugin.TextCommand):
    def run(self, edit, by="char"):
        grab_text(self.view, edit, by=by, forward=True)


# The following are convenience shortcuts:


class GranularMoveTextSubwordLeft(sublime_plugin.TextCommand):
    def run(self, edit):
        grab_text(self.view, edit, by='subword', forward=False)


class GranularMoveTextSubwordRight(sublime_plugin.TextCommand):
    def run(self, edit):
        grab_text(self.view, edit, by='subword', forward=True)


class GranularMoveTextToBol(sublime_plugin.TextCommand):
    def run(self, edit):
        grab_text(self.view, edit, by='bol')


class GranularMoveTextToEol(sublime_plugin.TextCommand):
    def run(self, edit):
        grab_text(self.view, edit, by='eol')


class GranularMoveTextUpTenTimes(sublime_plugin.TextCommand):
    def run(self, edit):
        grab_text(self.view, edit, by="line", forward=False, num_times=10)


class GranularMoveTextDownTenTimes(sublime_plugin.TextCommand):
    def run(self, edit):
        grab_text(self.view, edit, by="line", forward=True, num_times=10)


class GranularMoveTextUpThirtyTimes(sublime_plugin.TextCommand):
    def run(self, edit):
        grab_text(self.view, edit, by="line", forward=False, num_times=30)


class GranularMoveTextDownThirtyTimes(sublime_plugin.TextCommand):
    def run(self, edit):
        grab_text(self.view, edit, by="line", forward=True, num_times=30)


class SelectionIsEmptyOrReachesEolBol(sublime_plugin.EventListener):
    def on_query_context(self, view, key, operator, operand, match_all):
        if key != 'selection_is_empty_or_reaches_eol_bol':
            return None

        assert operand is True or operand is False

        if operator == sublime.OP_EQUAL:
            test = self.region_is_empty_or_is_full_lines

        elif operator == sublime.OP_NOT_EQUAL:
            test = self.region_is_not_full_lines

        else:
            assert False

        if match_all:
            return all(test(view, r) == operand for r in view.sel())

        else:
            return any(test(view, r) == operand for r in view.sel())

    def region_is_empty_or_is_full_lines(self, view, r):
        if r.a == r.b:
            return True

        line_a = view.full_line(r.a)
        line_b = view.full_line(r.b)

        if min(line_a.a, line_b.a) != r.begin():
            return False

        if max(line_a.a, line_b.a) != r.end():
            return False

        return True

    def region_is_not_full_lines(self, view, r):
        return not self.region_is_empty_or_is_full_lines(view, r)
