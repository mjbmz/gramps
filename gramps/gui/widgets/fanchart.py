#
# Gramps - a GTK+/GNOME based genealogy program
#
# Copyright (C) 2001-2007  Donald N. Allingham, Martin Hawlisch
# Copyright (C) 2009 Douglas S. Blank
# Copyright (C) 2012 Benny Malengier
# Copyright (C) 2013 Vassilii Khachaturov
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, 
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#

## Based on the paper:
##   http://www.cs.utah.edu/~draperg/research/fanchart/draperg_FHT08.pdf
## and the applet:
##   http://www.cs.utah.edu/~draperg/research/fanchart/demo/

## Found by redwood:
## http://www.gramps-project.org/bugs/view.php?id=2611

from __future__ import division

#-------------------------------------------------------------------------
#
# Python modules
#
#-------------------------------------------------------------------------
from gi.repository import Pango
from gi.repository import GObject
from gi.repository import Gdk
from gi.repository import Gtk
from gi.repository import PangoCairo
import cairo
import math
import colorsys
import sys
if sys.version_info[0] < 3:
    import cPickle as pickle
else:
    import pickle
from cgi import escape

#-------------------------------------------------------------------------
#
# GRAMPS modules
#
#-------------------------------------------------------------------------
from gramps.gen.db import DbTxn
from gramps.gen.display.name import displayer as name_displayer
from gramps.gen.errors import WindowActiveError
from gramps.gen.lib import ChildRef, Family, Name, Person, Surname
from gramps.gen.lib.date import Today
from ..editors import EditPerson, EditFamily
from .reorderfam import Reorder
from ..utils import color_graph_box, hex_to_rgb, is_right_click
from ..ddtargets import DdTargets
from gramps.gen.utils.alive import probably_alive
from gramps.gen.utils.libformatting import FormattingHelper
from gramps.gen.utils.db import (find_children, find_parents, find_witnessed_people,
                                 get_age, get_timeperiod, preset_name)
from gramps.gen.const import GRAMPS_LOCALE as glocale
_ = glocale.translation.gettext
from gramps.gui.utilscairo import warpPath

#-------------------------------------------------------------------------
#
# Constants
#
#-------------------------------------------------------------------------

PIXELS_PER_GENERATION = 50 # size of radius for generation
BORDER_EDGE_WIDTH = 10     # empty white box size at edge to indicate parents
CHILDRING_WIDTH = 12       # width of the children ring inside the person
TRANSLATE_PX = 10          # size of the central circle, used to move the chart
PAD_PX = 4                 # padding with edges
PAD_TEXT = 2               # padding for text in boxes

BACKGROUND_SCHEME1 = 0
BACKGROUND_SCHEME2 = 1
BACKGROUND_GENDER = 2
BACKGROUND_WHITE = 3
BACKGROUND_GRAD_GEN = 4
BACKGROUND_GRAD_AGE = 5
BACKGROUND_SINGLE_COLOR = 6
BACKGROUND_GRAD_PERIOD = 7
GENCOLOR = {
    BACKGROUND_SCHEME1: ((255, 63,  0),
                         (255,175, 15),
                         (255,223, 87),
                         (255,255,111),
                         (159,255,159),
                         (111,215,255),
                         ( 79,151,255),
                         (231, 23,255),
                         (231, 23,121),
                         (210,170,124),
                         (189,153,112)),
    BACKGROUND_SCHEME2: ((229,191,252),
                         (191,191,252),
                         (191,222,252),
                         (183,219,197),
                         (206,246,209)),
    BACKGROUND_WHITE: ((255,255,255),
                       (255,255,255),),
    }

MAX_AGE = 100
GRADIENTSCALE = 5

FORM_CIRCLE = 0
FORM_HALFCIRCLE = 1
FORM_QUADRANT = 2

COLLAPSED = 0
NORMAL = 1
EXPANDED = 2

TYPE_BOX_NORMAL = 0
TYPE_BOX_FAMILY = 1

#-------------------------------------------------------------------------
#
# FanChartBaseWidget
#
#-------------------------------------------------------------------------

class FanChartBaseWidget(Gtk.DrawingArea):
    """ a base widget for fancharts"""
    CENTER = 60                # pixel radius of center, changes per fanchart

    def __init__(self, dbstate, uistate, callback_popup=None):
        GObject.GObject.__init__(self)
        self.radialtext = True
        st_cont = self.get_style_context()
        col = st_cont.lookup_color('text_color')
        if col[0]:
            self.textcolor = (col[1].red, col[1].green, col[1].blue)
        else:
            self.textcolor = (0, 0, 0)
        self.dbstate = dbstate
        self.uistate = uistate
        self.translating = False
        self.goto = None
        self.on_popup = callback_popup
        self.last_x, self.last_y = None, None
        self.fontdescr = "Sans"
        self.fontsize = 8
        self.twolineformat_nums=(name_displayer.add_name_format('fanchart_name_line1', '%l'),
                                 name_displayer.add_name_format('fanchart_name_line2', '%f %s'))
        self.connect("button_release_event", self.on_mouse_up)
        self.connect("motion_notify_event", self.on_mouse_move)
        self.connect("button-press-event", self.on_mouse_down)
        #we want to grab key events also
        self.set_can_focus(True)
        self.connect("key-press-event", self.on_key_press)
        
        self.connect("draw", self.on_draw)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                        Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.POINTER_MOTION_MASK |
                        Gdk.EventMask.KEY_PRESS_MASK)

        # Enable drag
        self.drag_source_set(Gdk.ModifierType.BUTTON1_MASK,
                            [],
                            Gdk.DragAction.COPY)
        tglist = Gtk.TargetList.new([])
        tglist.add(DdTargets.PERSON_LINK.atom_drag_type,
                   DdTargets.PERSON_LINK.target_flags,
                   DdTargets.PERSON_LINK.app_id)
        #allow drag to a text document, info on drag_get will be 0L !
        tglist.add_text_targets(0)
        self.drag_source_set_target_list(tglist)
        self.connect("drag_data_get", self.on_drag_data_get)
        self.connect("drag_begin", self.on_drag_begin)
        self.connect("drag_end", self.on_drag_end)
        # Enable drop
        self.drag_dest_set(Gtk.DestDefaults.MOTION |
                            Gtk.DestDefaults.DROP,
                            [],
                            Gdk.DragAction.COPY)
        tglist = Gtk.TargetList.new([])
        tglist.add(DdTargets.PERSON_LINK.atom_drag_type,
                   DdTargets.PERSON_LINK.target_flags,
                   DdTargets.PERSON_LINK.app_id)
        self.drag_dest_set_target_list(tglist)
        self.connect('drag_data_received', self.on_drag_data_received)

        self._mouse_click = False
        self.rotate_value = 90 # degrees, initially, 1st gen male on right half
        self.center_delta_xy = [0, 0] # translation of the center of the fan wrt canonical center
        self.center_xy = [0, 0] # coord of the center of the fan
        self.mouse_x = 0
        self.mouse_y = 0
        #(re)compute everything
        self.reset()
        self.set_size_request(120, 120)

    def __del__(self):
        for num in self.twolineformat_nums:
            name_displayer.del_name_format(num)

    def reset(self):
        """
        Reset the fan chart. This should trigger computation of all data 
        structures needed
        """
        self.cache_fontcolor = {}
        
        # fill the data structure
        self._fill_data_structures()
    
        # prepare the colors for the boxes 
        self.prepare_background_box(self.generations)

    def _fill_data_structures(self):
        """
        fill in the data structures that will be needed to draw the chart
        """
        raise NotImplementedError
        
    def do_size_request(self, requisition):
        """
        Overridden method to handle size request events.
        """
        if self.form == FORM_CIRCLE:
            requisition.width = 2 * self.halfdist()
            requisition.height = requisition.width
        elif self.form == FORM_HALFCIRCLE:
            requisition.width = 2 * self.halfdist()
            requisition.height = requisition.width / 2 + self.CENTER + PAD_PX
        elif self.form == FORM_QUADRANT:
            requisition.width = self.halfdist() + self.CENTER + PAD_PX
            requisition.height = requisition.width

    def do_get_preferred_width(self):
        """ GTK3 uses width for height sizing model. This method will 
            override the virtual method
        """
        req = Gtk.Requisition()
        self.do_size_request(req)
        return req.width, req.width

    def do_get_preferred_height(self):
        """ GTK3 uses width for height sizing model. This method will 
            override the virtual method
        """
        req = Gtk.Requisition()
        self.do_size_request(req)
        return req.height, req.height

    def halfdist(self):
        """
        Compute the half radius of the circle
        """
        raise NotImplementedError

    def gen_pixels(self):
        """
        how many pixels a generation takes up in the fanchart
        """
        raise NotImplementedError
    
    def get_radiusinout_for_generation(self,generation):
        raise NotImplementedError

    def on_draw(self, widget, cr, scale=1.):
        """
        callback to draw the fanchart
        """
        raise NotImplementedError

    def people_generator(self):
        """
        a generator over all people outside of the core person
        """
        raise NotImplementedError

    def innerpeople_generator(self):
        """
        a generator over all people inside of the core person
        """
        raise NotImplementedError

    def set_userdata_timeperiod(self, person, userdata):
        """
        set the userdata as used by timeperiod
        """
        period = None
        if person:
            period = get_timeperiod(self.dbstate.db, person)
            if period is not None:
                if period > self.maxperiod:
                    self.maxperiod = period
                if period < self.minperiod:
                    self.minperiod = period
        userdata.append(period)

    def set_userdata_age(self, person, userdata):
        agecol = (1, 1, 1)  # white
        if person:
            age = get_age(self.dbstate.db, person)
            if age is not None:
                age = age[0]
                if age < 0:
                    age = 0
                elif age > MAX_AGE:
                    age = MAX_AGE
                #now determine fraction for gradient
                agefrac = age / MAX_AGE
                agecol = colorsys.hsv_to_rgb(
                (1-agefrac) * self.cstart_hsv[0] + agefrac * self.cend_hsv[0],
                (1-agefrac) * self.cstart_hsv[1] + agefrac * self.cend_hsv[1],
                (1-agefrac) * self.cstart_hsv[2] + agefrac * self.cend_hsv[2],
                    )
        userdata.append((agecol[0]*255, agecol[1]*255, agecol[2]*255))

    def prepare_background_box(self, maxgen):
        """
        Method that is called every reset of the chart, to precomputed values
        needed for the background of the boxes
        """
        cstart = hex_to_rgb(self.grad_start)
        cend = hex_to_rgb(self.grad_end)
        self.cstart_hsv = colorsys.rgb_to_hsv(cstart[0]/255, cstart[1]/255, 
                                         cstart[2]/255)
        self.cend_hsv = colorsys.rgb_to_hsv(cend[0]/255, cend[1]/255, 
                                       cend[2]/255)
        if self.background in [BACKGROUND_GENDER, BACKGROUND_SINGLE_COLOR]:
            # nothing to precompute
            self.colors =  None
            self.maincolor = cstart
        elif self.background == BACKGROUND_GRAD_GEN:
            #compute the colors, -1, 0, ..., maxgen
            divs = [x/(maxgen-1) for x in range(maxgen)] if maxgen>1 else [0]
            rgb_colors = [colorsys.hsv_to_rgb(
                            (1-x) * self.cstart_hsv[0] + x * self.cend_hsv[0], 
                            (1-x) * self.cstart_hsv[1] + x * self.cend_hsv[1],
                            (1-x) * self.cstart_hsv[2] + x * self.cend_hsv[2],
                            ) for x in divs]
            self.colors = [(255*r, 255*g, 255*b) for r, g, b in rgb_colors]
        elif self.background == BACKGROUND_GRAD_PERIOD:
            # we fill in in the data structure what the period is, None if not found
            self.colors =  None
            self.minperiod = 1e10
            self.maxperiod = -1e10
            gen_people = self.people_generator()
            for person, userdata in gen_people:
                self.set_userdata_timeperiod(person, userdata)
            # same for child
            gen_inner = self.innerpeople_generator()
            for child, userdata in gen_inner:
                self.set_userdata_timeperiod(child, userdata)
            #now create gradient data, 5 values from min to max rounded to nearest 50
            if self.maxperiod < self.minperiod:
                self.maxperiod = self.minperiod = Today().get_year()
            rper = self.maxperiod // 50
            if rper * 50 != self.maxperiod:
                self.maxperiod = rper * 50 + 50
            self.minperiod = 50 * (self.minperiod // 50)
            periodrange = self.maxperiod - self.minperiod
            steps = 2 * GRADIENTSCALE - 1
            divs = [x/(steps-1) for x in range(steps)]
            self.gradval = ['%d' % int(self.minperiod + x * periodrange) for x in divs]
            for i in range(len(self.gradval)):
                if i % 2 == 1:
                    self.gradval[i] = ''
            self.gradcol = [colorsys.hsv_to_rgb(
                        (1-div) * self.cstart_hsv[0] + div * self.cend_hsv[0], 
                        (1-div) * self.cstart_hsv[1] + div * self.cend_hsv[1],
                        (1-div) * self.cstart_hsv[2] + div * self.cend_hsv[2],
                        ) for div in divs]
            
        elif self.background == BACKGROUND_GRAD_AGE:
            # we fill in in the data structure what the color age is, white if no age
            self.colors =  None
            gen_people = self.people_generator()
            for person, userdata in gen_people:
                self.set_userdata_age(person, userdata)
            # same for child
            gen_inner = self.innerpeople_generator()
            for child, userdata in gen_inner:
                self.set_userdata_age(child, userdata)
            #now create gradient data, 5 values from 0 to max
            steps = 2 * GRADIENTSCALE - 1
            divs = [x/(steps-1) for x in range(steps)]
            self.gradval = ['%d' % int(x * MAX_AGE) for x in divs]
            self.gradval[-1] = '%d+' % MAX_AGE
            for i in range(len(self.gradval)):
                if i % 2 == 1:
                    self.gradval[i] = ''
            self.gradcol = [colorsys.hsv_to_rgb(
                        (1-div) * self.cstart_hsv[0] + div * self.cend_hsv[0], 
                        (1-div) * self.cstart_hsv[1] + div * self.cend_hsv[1],
                        (1-div) * self.cstart_hsv[2] + div * self.cend_hsv[2],
                        ) for div in divs]
        else:
            # known colors per generation, set or compute them
            self.colors = GENCOLOR[self.background]

    def background_box(self, person, generation, userdata):
        """
        determine red, green, blue value of background of the box of person,
        which has gender gender, and is in ring generation
        """
        if generation == 0 and self.background in [BACKGROUND_GENDER, 
                BACKGROUND_GRAD_GEN, BACKGROUND_SCHEME1,
                BACKGROUND_SCHEME2]:
            # white for center person:
            color = (255, 255, 255)
        elif self.background == BACKGROUND_GENDER:
            try:
                alive = probably_alive(person, self.dbstate.db)
            except RuntimeError:
                alive = False
            backgr, border = color_graph_box(alive, person.gender)
            color = hex_to_rgb(backgr)
        elif self.background == BACKGROUND_SINGLE_COLOR:
            color = self.maincolor
        elif self.background == BACKGROUND_GRAD_AGE:
            color = userdata[0]
        elif self.background == BACKGROUND_GRAD_PERIOD:
            period = userdata[0]
            if period is None:
                color = (255, 255, 255)  # white
            else:
                if self.maxperiod != self.minperiod:
                    periodfrac = ((period - self.minperiod)
                                  / (self.maxperiod - self.minperiod))
                else:
                    periodfrac = 0.5
                periodcol = colorsys.hsv_to_rgb(
            (1-periodfrac) * self.cstart_hsv[0] + periodfrac * self.cend_hsv[0], 
            (1-periodfrac) * self.cstart_hsv[1] + periodfrac * self.cend_hsv[1],
            (1-periodfrac) * self.cstart_hsv[2] + periodfrac * self.cend_hsv[2],
                        )
                color = (periodcol[0]*255, periodcol[1]*255, periodcol[2]*255)
        else:
            if self.background == BACKGROUND_GRAD_GEN and generation < 0:
                generation = 0
            color = self.colors[generation % len(self.colors)]
            if person.gender == Person.MALE:
                color = [x*.9 for x in color]
        # now we set transparency data
        if self.filter and not self.filter.match(person.handle, self.dbstate.db):
            if self.background == BACKGROUND_SINGLE_COLOR:
                alpha = 0.  # no color shown
            else:
                alpha = self.alpha_filter
        else:
            alpha = 1.
        
        return color[0], color[1], color[2], alpha

    def fontcolor(self, r, g, b, a):
        """
        return the font color based on the r, g, b of the background
        """
        if a == 0:
            return self.textcolor
        try:
            return self.cache_fontcolor[(r, g, b)]
        except KeyError:
            hls = colorsys.rgb_to_hls(r/255, g/255, b/255)
            # we use the lightness value to determine white or black font
            if hls[1] > 0.4:
                self.cache_fontcolor[(r, g, b)] = (0, 0, 0)
            else:
                self.cache_fontcolor[(r, g, b)] = (1, 1, 1)
        return self.cache_fontcolor[(r, g, b)]

    def fontbold(self, a):
        """
        The font should be bold if no transparency and font is set.
        In that case, True is returned
        """
        if a >= 1. and self.filter:
            return True
        return False

    def draw_radbox(self, cr, radiusin, radiusout, start_rad, stop_rad, color,
                    thick=False):
        """
        Procedure to draw a person in the outter ring position
        """
        cr.move_to(radiusout * math.cos(start_rad), radiusout * math.sin(start_rad))
        cr.arc(0, 0, radiusout, start_rad, stop_rad)
        cr.line_to(radiusin * math.cos(stop_rad), radiusin * math.sin(stop_rad))
        cr.arc_negative(0, 0, radiusin, stop_rad, start_rad)
        cr.close_path()
        ##path = cr.copy_path() # not working correct
        cr.set_source_rgba(color[0], color[1], color[2], color[3])
        cr.fill()
        #and again for the border
        cr.move_to(radiusout * math.cos(start_rad), radiusout * math.sin(start_rad))
        cr.arc(0, 0, radiusout, start_rad, stop_rad)
        cr.line_to(radiusin * math.cos(stop_rad), radiusin * math.sin(stop_rad))
        cr.arc_negative(0, 0, radiusin, stop_rad, start_rad)
        cr.close_path()
        ##cr.append_path(path) # not working correct
        cr.set_source_rgb(0, 0, 0) # black
        if thick:
            cr.set_line_width(3)
        else:
            cr.set_line_width(1)
        cr.stroke()
        cr.set_line_width(1)

    def draw_innerring(self, cr, person, userdata, start, inc):
        """
        Procedure to draw a person in the inner ring position
        """
        # in polar coordinates what is to draw
        rmin = TRANSLATE_PX
        rmax = TRANSLATE_PX + CHILDRING_WIDTH
        thetamin = start
        thetamax = start + inc
        # add child to angle storage
        self.angle[-2].append([thetamin, thetamax, None])
        #draw child now
        cr.move_to(rmin*math.cos(thetamin), rmin*math.sin(thetamin))
        cr.arc(0, 0, rmin, thetamin, thetamax)
        cr.line_to(rmax*math.cos(thetamax), rmax*math.sin(thetamax))
        cr.arc_negative(0, 0, rmax, thetamax, thetamin)
        cr.close_path()
        ##path = cr.copy_path() # not working correct
        cr.set_source_rgb(0, 0, 0) # black
        cr.set_line_width(1)
        cr.stroke()
        #now again to fill
        if person:
            r, g, b, a = self.background_box(person, -1, userdata)
        else:
            r=255; g=255; b=255; a=1
        cr.move_to(rmin*math.cos(thetamin), rmin*math.sin(thetamin))
        cr.arc(0, 0, rmin, thetamin, thetamax)
        cr.line_to(rmax*math.cos(thetamax), rmax*math.sin(thetamax))
        cr.arc_negative(0, 0, rmax, thetamax, thetamin)
        cr.close_path()
        ##cr.append_path(path) # not working correct
        cr.set_source_rgba(r/255., g/255., b/255., a) 
        cr.fill()

    def draw_person_text(self, cr, person, radiusin, radiusout, start, stop,
                  radial=False, fontcolor=(0, 0, 0), bold=False):
        if not person: return
        draw_radial = radial and self.radialtext
        if not self.twolinename:
            name=name_displayer.display(person)
            self.draw_text(cr, name, radiusin, radiusout, start, stop, draw_radial, 
                       fontcolor, bold)
        else:
            text=name_displayer.display(person)
            text_line1=name_displayer.display_format(person,self.twolineformat_nums[0])
            text_line2=name_displayer.display_format(person,self.twolineformat_nums[1])
            if draw_radial:
                split_frac_line1=0.5
                if (math.degrees(start) + self.rotate_value - 90) % 360 < 179 and self.flipupsidedownname:
                    middle=(start*split_frac_line1+stop*(1.0-split_frac_line1))
                    (a11,a12,a21,a22)=(middle,stop,start,middle)
                else:
                    middle=(start*(1.0-split_frac_line1)+stop*split_frac_line1)
                    (a11,a12,a21,a22)=(start,middle,middle,stop)
                written_textwidth=self.draw_text(cr, text_line1, radiusin, radiusout, a11, a12, draw_radial, fontcolor, bold=1)
                if written_textwidth == 0 and text_line1 != "":
                    #Not enought space for 2 line, fallback to 1 line
                    written_textwidth=self.draw_text(cr, text_line1, radiusin, radiusout, start, stop, draw_radial, fontcolor, bold=1)
                    self.draw_text(cr, text_line2, radiusin+written_textwidth+PAD_TEXT, radiusout, start, stop, draw_radial, fontcolor, bold)
                else:
                    self.draw_text(cr, text_line2, radiusin, radiusout, a21, a22, draw_radial, fontcolor, bold)
            else:
                middle=(radiusin*.5+radiusout*.5)
                self.draw_text(cr, text_line1, middle, radiusout, start, stop, draw_radial, fontcolor, bold=1)
                self.draw_text(cr, text_line2, radiusin, middle, start, stop, draw_radial, fontcolor, bold=0)

    def wrap_truncate_layout(self, layout, font, width_pixels, height_pixels, tryrescale=True):
        """
        Uses the layout to wrap and truncate its text to given width
        Returns: (w,h) as returned by layout.get_pixel_size()
        """
        all_text_backup = layout.get_text()
        layout.set_font_description(font)
        layout.set_width(Pango.SCALE * width_pixels)

        # NOTE: one may not truncate the text to just the 1st line's text,
        # because the truncation can create invalid Unicode.
        if layout.get_line_count() > 1:
            layout.set_text(layout.get_text(), layout.get_line(0).length)

        #2. we check if height is ok
        w, h = layout.get_pixel_size()
        if h > height_pixels:
            if tryrescale:
                #try to reduce the height
                fontsize = max(height_pixels / h * font.get_size() /1.1, font.get_size()/2.0)
                font.set_size(fontsize)
                layout.set_text(all_text_backup, len(all_text_backup)) # reducing the height allows for more characters
                layout.set_font_description(font)
                if layout.get_line_count() > 1:
                    layout.set_text(layout.get_text(), layout.get_line(0).length)
                w, h = layout.get_pixel_size()
            # we check again if height is ok
            if h > height_pixels:
                #we could not fix it, no text
                layout.set_text("",0)
        layout.context_changed()
        return layout.get_pixel_size()

    def draw_text(self, cr, text, radiusin, radiusout, start_rad, stop_rad,
                  radial=False,
                  fontcolor=(0, 0, 0), bold=False):
        """
        Display text at a particular radius, between start_rad and stop_rad
        radians.
        """
        font = Pango.FontDescription(self.fontdescr)
        fontsize = self.fontsize
        font.set_size(fontsize * Pango.SCALE)
        if bold:
            font.set_weight(Pango.Weight.BOLD)
        cr.save()
        cr.set_source_rgb(*fontcolor)
        if radial and self.radialtext:
            self.draw_radial_text(cr, text, radiusin, radiusout, start_rad, stop_rad, font)
        else:
            self.draw_arc_text(cr, text, radiusin, radiusout, start_rad, stop_rad, font)
        cr.restore()

    def draw_radial_text(self, cr, text, radiusin, radiusout, start_rad, stop_rad, font):
        layout = self.create_pango_layout(text)
        layout.set_font_description(font)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)

        # compute available text space
        # NOTE: for radial text, the sector radius height is the text width 
        avail_height = (stop_rad - start_rad) * radiusin - 2.0 * PAD_TEXT 
        avail_width = radiusout - radiusin - 2.0 * PAD_TEXT

        w, h = self.wrap_truncate_layout(layout, font, avail_width, avail_height, tryrescale=True)

        #  2. now draw this text
        # offset for cairo-font system is 90
        if (math.degrees(start_rad) + self.rotate_value - 90) % 360 < 179 and self.flipupsidedownname:
            angle = (start_rad + stop_rad)/2 + (h / radiusin / 2) + math.pi
            start_pos = -radiusout + PAD_TEXT
        else:
            angle = (start_rad + stop_rad)/2 - (h / radiusin / 2)
            start_pos = radiusin + PAD_TEXT
        cr.rotate(angle)
        layout.context_changed()
        cr.move_to(start_pos, 0)
        PangoCairo.show_layout(cr, layout)

    def draw_arc_text(self, cr, text, radiusin, radiusout, start_rad, stop_rad, font):
        """
        Display text at a particular radius, between start and stop
        degrees, setting it up along the arc, center-justified.

        Text not fitting a single line will be char-wrapped away.
        """
        layout = self.create_pango_layout(text)
        layout.set_font_description(font)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)

        # get height of text:
        textheight=layout.get_size()[1]/Pango.SCALE
        radius_text=(radiusin+radiusout)/2.0 #+ textheight/2.0

        # 1. compute available text space
        avail_height = radiusout - radiusin - 2.0 * PAD_TEXT
        avail_width = (stop_rad - start_rad) * radius_text - 2.0 * PAD_TEXT

        w, h = self.wrap_truncate_layout(layout, font, avail_width, avail_height, tryrescale=True)

        rad_padding = PAD_TEXT / radius_text
        # 2. Compute text position start angle
        pos_rad = start_rad + math.pi/2 + rad_padding # offset for cairo-font system is 90:
        cr.rotate(pos_rad)
        cr.new_path()
        cr.move_to(0, -radiusin)
        rad_spread = avail_width / radius_text

        # 3. Use the layout to provide us the metrics of the text box
        PangoCairo.layout_path(cr, layout)
        #le = layout.get_line(0).get_pixel_extents()[0]
        pe = cr.path_extents()
        if pe == (0.0, 0.0, 0.0, 0.0):
            # 7710: When scrolling the path extents are zero on Ubuntu 14.04
            return
        arc_used_ratio = w / (radius_text * rad_spread)
        rad_mid = pos_rad + rad_spread/2

        # 4. The moment of truth: map the text box onto the sector, and render!
        warpPath(cr, \
            self.create_map_rect_to_sector(radius_text, pe, \
                arc_used_ratio, rad_mid - rad_spread/2, rad_mid + rad_spread/2))
        cr.fill()

    @staticmethod
    def create_map_rect_to_sector(radius, rect, arc_used_ratio, start_rad, stop_rad):
        """
        Create a 2D-transform, mapping a rectangle onto a circle sector.

        :param radius: average radius of the target sector
        :param rect: (x1, y1, x2, y2)
        :param arc_used_ratio: From 0.0 to 1.0. Rather than stretching onto the
                               whole sector, only the middle arc_used_ratio part
                               will be mapped onto.
        :param start_rad: start radial angle of the sector, in radians
        :param stop_rad: stop radial angle of the sector, in radians
        :returns: a lambda (x,y)|->(xNew,yNew) to feed to warpPath.
        """

        x0, y0, w, h = rect[0], rect[1], rect[2]-rect[0], rect[3]-rect[1]

        radiusin = radius - h/2
        radiusout = radius + h/2
        drho = h
        dphi = (stop_rad - start_rad)

        # There has to be a clearer way to express this transform,
        # by stacking a set of transforms on cr around using this function
        # and doing a mapping between unit squares of rectangular and polar
        # coordinates.

        def phi(x):
            return (x - x0) * dphi * arc_used_ratio / w \
                   + (1 - arc_used_ratio) * dphi / 2 \
                   - math.pi/2 
        def rho(y):
            return (y - y0) * (radiusin - radiusout)/h + radiusout

        # In (user coordinates units - pixels):
        # x from x0 to x0 + w
        # y from y0 to y0 + h
        # Out:
        # (x, y) within the arc_used_ratio of a box like drawn by draw_radbox
        return lambda x, y: \
            (rho(y) * math.cos(phi(x)), rho(y) * math.sin(phi(x)))

    def draw_gradient_legend(self, cr, widget, halfdist):
        gradwidth = 10
        gradheight = 10
        starth = 15
        startw = 5
        cr.save()

        cr.translate(-self.center_xy[0], -self.center_xy[1])

        font = Pango.FontDescription(self.fontdescr)
        fontsize = self.fontsize
        font.set_size(fontsize * Pango.SCALE)
        for color, text in zip(self.gradcol, self.gradval):
            cr.move_to(startw, starth)
            cr.rectangle(startw, starth, gradwidth, gradheight)
            cr.set_source_rgb(color[0], color[1], color[2])
            cr.fill()
            layout = self.create_pango_layout(text)
            layout.set_font_description(font)
            cr.move_to(startw+gradwidth+4, starth)
            cr.set_source_rgb(0, 0, 0) #black
            PangoCairo.show_layout(cr, layout)
            starth = starth+gradheight
        cr.restore()

    def person_under_cursor(self, curx, cury):
        """
        Determine the generation and the position in the generation at 
        position x and y, as well as the type of box. 
        generation = -1 on center black dot
        generation >= self.generations outside of diagram
        """
        raise NotImplementedError

    def boxtype(self, radius):
        """
        default is only one type of box type
        """
        return TYPE_BOX_NORMAL

    def personpos_at_angle(self, generation, angledeg, btype):
        """
        returns the person in generation generation at angle of type btype.
        """
        raise NotImplementedError

    def person_at(self, generation, pos, btype):
        """
        returns the person at generation, pos, btype
        """
        raise NotImplementedError

    def family_at(self, generation, pos, btype):
        """
        returns the family at generation, pos, btype
        """
        raise NotImplementedError

    def _have_children(self, person):
        """
        Returns True if a person has children.
        TODO: is there no util function for this
        """
        if person:
            for family_handle in person.get_family_handle_list():
                family = self.dbstate.db.get_family_from_handle(family_handle)
                if family and len(family.get_child_ref_list()) > 0:
                    return True
        return False

    def on_key_press(self, widget, eventkey):
        """grab key press
        """
        if self.mouse_x and self.mouse_y:
            generation, selected, btype = self.person_under_cursor(self.mouse_x,
                                                self.mouse_y)
            if selected is None:
                return False
            person = self.person_at(generation, selected, btype)
            family = self.family_at(generation, selected, btype)
            if person and (Gdk.keyval_name(eventkey.keyval) == 'e'):
                # we edit the person
                self.edit_person_cb(None, person.handle)
                return True
            elif family and (Gdk.keyval_name(eventkey.keyval) == 'f'):
                # we edit the family
                self.edit_fam_cb(None, family.handle)
                return True
            
        return False

    def on_mouse_down(self, widget, event):
        self.translating = False # keep track of up/down/left/right movement
        generation, selected, btype = self.person_under_cursor(event.x, event.y)

        if event.button == 1:
            #we grab the focus to enable to see key_press events
            self.grab_focus()

        # left mouse on center dot, we translate on left click
        if generation == -1: 
            if event.button == 1: # left mouse
                # save the mouse location for movements
                self.translating = True
                self.last_x, self.last_y = event.x, event.y
                return True

        #click in open area, prepare for a rotate
        if selected is None:
            # save the mouse location for movements
            self.last_x, self.last_y = event.x, event.y
            return True

        #left click on person, prepare for expand/collapse or drag
        if event.button == 1:
            self._mouse_click = True
            self._mouse_click_gen = generation
            self._mouse_click_sel = selected
            self._mouse_click_btype = btype
            return False
    
        #right click on person, context menu
        # Do things based on state, event.get_state(), or button, event.button
        if is_right_click(event):
            person = self.person_at(generation, selected, btype)
            family = self.family_at(generation, selected, btype)
            fhandle = None
            if family:
                fhandle = family.handle
            if person and self.on_popup:
                self.on_popup(widget, event, person.handle, fhandle)
                return True

        return False

    def on_mouse_move(self, widget, event):
        self._mouse_click = False
        if self.last_x is None or self.last_y is None:
            # while mouse is moving, we must update the tooltip based on person
            generation, selected, btype = self.person_under_cursor(event.x, event.y)
            self.mouse_x = event.x
            self.mouse_y = event.y
            tooltip = ""
            person = self.person_at(generation, selected, btype)
            if person:
                tooltip = self.format_helper.format_person(person, 11)
            self.set_tooltip_text(tooltip)
            return False
        
        #translate or rotate should happen
        if self.translating:
            canonical_center = self.center_xy_from_delta([0,0])
            self.center_delta_xy = canonical_center[0]-event.x,canonical_center[1]-event.y
            self.center_xy = self.center_xy_from_delta()
        else:
            # get the angles of the two points from the center:
            start_angle = math.atan2(event.y - self.center_xy[1], event.x - self.center_xy[0])
            end_angle = math.atan2(self.last_y - self.center_xy[1], self.last_x - self.center_xy[0])
            # now look at change in angle:
            diff_angle = (end_angle - start_angle) % (math.pi * 2.0)
            self.rotate_value -= math.degrees(diff_angle)
            self.last_x, self.last_y = event.x, event.y
        self.queue_draw()
        return True

    def center_xy_from_delta(self, delta=None):
        alloc = self.get_allocation()
        x, y, w, h = alloc.x, alloc.y, alloc.width, alloc.height
        if delta is None: delta = self.center_delta_xy
        if self.form == FORM_CIRCLE:
            canvas_xy = w/2 - delta[0], h/2 - delta[1]
        elif self.form == FORM_HALFCIRCLE:
            canvas_xy = w/2 - delta[0], h - self.CENTER - PAD_PX - delta[1]
        elif self.form == FORM_QUADRANT:
            canvas_xy = self.CENTER + PAD_PX - delta[0], h - self.CENTER - PAD_PX - delta[1]
        return canvas_xy
    
    def do_mouse_click(self):
        """
        action to take on left mouse click
        """
        pass

    def on_mouse_up(self, widget, event):
        if self._mouse_click:
            self.do_mouse_click()
            return True
        if self.last_x is None or self.last_y is None:
            # No translate or rotate
            return True
        if self.translating:
            self.translating = False
        
        self.last_x, self.last_y = None, None
        self.queue_draw()
        return True

    def on_drag_begin(self, widget, data):
        """Set up some inital conditions for drag. Set up icon."""
        self.in_drag = True
        self.drag_source_set_icon_stock('gramps-person')

    def on_drag_end(self, widget, data):
        """Set up some inital conditions for drag. Set up icon."""
        self.in_drag = False

    def on_drag_data_get(self, widget, context, sel_data, info, time):
        """
        Returned parameters after drag.
        Specified for 'person-link', for others return text info about person.
        """
        tgs = [x.name() for x in context.list_targets()]
        person = self.person_at(self._mouse_click_gen, self._mouse_click_sel,
                                self._mouse_click_btype)
        if info == DdTargets.PERSON_LINK.app_id:
            data = (DdTargets.PERSON_LINK.drag_type,
                    id(self), person.get_handle(), 0)
            sel_data.set(sel_data.get_target(), 8, pickle.dumps(data))
        elif ('TEXT' in tgs or 'text/plain' in tgs) and info == 0:
            sel_data.set_text(self.format_helper.format_person(person, 11), -1)

    def on_drag_data_received(self, widget, context, x, y, sel_data, info, time):
        """
        Handle the standard gtk interface for drag_data_received.

        If the selection data is defined, extract the value from sel_data.data
        """
        gen, persatcurs, btype = self.person_under_cursor(x, y)
        if gen == -1 or gen == 0:
            if sel_data and sel_data.get_data():
                (drag_type, idval, handle, val) = pickle.loads(sel_data.get_data())
                self.goto(self, handle)

    def edit_person_cb(self, obj, person_handle):
        person = self.dbstate.db.get_person_from_handle(person_handle)
        if person:
            try:
                EditPerson(self.dbstate, self.uistate, [], person)
            except WindowActiveError:
                pass
            return True
        return False

    def edit_fam_cb(self, obj, family_handle):
        fam = self.dbstate.db.get_family_from_handle(family_handle)
        if fam:
            try:
                EditFamily(self.dbstate, self.uistate, [], fam)
            except WindowActiveError:
                pass
            return True
        return False
    
#-------------------------------------------------------------------------
#
# FanChartWidget
#
#-------------------------------------------------------------------------

class FanChartWidget(FanChartBaseWidget):
    """
    Interactive Fan Chart Widget. 
    """

    def __init__(self, dbstate, uistate, callback_popup=None):
        """
        Fan Chart Widget. Handles visualization of data in self.data.
        See main() of FanChartGramplet for example of model format.
        """
        self.set_values(None, 9, BACKGROUND_GRAD_GEN, True, True, True, True, 'Sans', '#0000FF',
                    '#FF0000', None, 0.5, FORM_CIRCLE)
        FanChartBaseWidget.__init__(self, dbstate, uistate, callback_popup)

    def set_values(self, root_person_handle, maxgen, background, childring, 
              flipupsidedownname, twolinename, radialtext, fontdescr, 
              grad_start, grad_end, filter, alpha_filter, form):
        """
        Reset the values to be used:
         
        :param root_person_handle: person to show
        :param maxgen: maximum generations to show
        :param background: config setting of which background procedure to use
        :type background: int
        :param childring: to show the center ring with children or not
        :param twolinename: uses two lines for the display of person's name
        :param flipupsidedownname: flip name on the left of the fanchart for the display of person's name
        :param radialtext: try to use radial text or not
        :param fontdescr: string describing the font to use
        :param grad_start: colors to use for background procedure
        :param grad_end: colors to use for background procedure
        :param filter: the person filter to apply to the people in the chart
        :param alpha: the alpha transparency value (0-1) to apply to filtered
                      out data
        :param form: the ``FORM_`` constant for the fanchart 
        """
        self.rootpersonh = root_person_handle
        self.generations = maxgen
        self.radialtext = radialtext
        self.childring = childring
        self.twolinename = twolinename
        self.flipupsidedownname = flipupsidedownname
        self.background = background
        self.fontdescr = fontdescr
        self.grad_start = grad_start
        self.grad_end = grad_end
        self.filter = filter
        self.alpha_filter = alpha_filter
        self.form = form

    def set_generations(self):
        """
        Set the generations to max, and fill data structures with initial data.
        """
        self.angle = {}
        if self.childring:
            self.angle[-2] = []
        self.data = {}
        self.childrenroot = []
        self.rootangle_rad = [math.radians(0), math.radians(360)]
        if self.form == FORM_HALFCIRCLE:
            self.rootangle_rad = [math.radians(90), math.radians(270)]
        elif self.form == FORM_QUADRANT:
            self.rootangle_rad = [math.radians(180), math.radians(270)]
        for i in range(self.generations):
            # name, person, parents?, children?
            self.data[i] = [(None,) * 5] * 2 ** i
            self.angle[i] = []
            angle = self.rootangle_rad[0]
            slice = 1/ (2 ** i) * (self.rootangle_rad[1] - self.rootangle_rad[0])
            for count in range(len(self.data[i])):
                # start, stop, state
                self.angle[i].append([angle, angle + slice, NORMAL])
                angle += slice

    def _fill_data_structures(self):
        self.set_generations()
        person = self.dbstate.db.get_person_from_handle(self.rootpersonh)
        if not person: 
            name = None
        else:
            name = name_displayer.display(person)
        parents = self._have_parents(person)
        child = self._have_children(person)
        # our data structure is the text, the person object, parents, child and
        # list for userdata which we might fill in later.
        self.data[0][0] = (name, person, parents, child, [])
        self.childrenroot = []
        if child:
            childlist = find_children(self.dbstate.db, person)
            for child_handle in childlist:
                child = self.dbstate.db.get_person_from_handle(child_handle)
                if not child:
                    continue
                else:
                    self.childrenroot.append((name_displayer.display(child), child, True,
                                              self._have_children(child), []))
        for current in range(1, self.generations):
            parent = 0
            # name, person, parents, children
            for (n, p, q, c, d) in self.data[current - 1]:
                # Get father's and mother's details:
                for person in [self._get_parent(p, True), self._get_parent(p, False)]:
                    if person:
                        name = name_displayer.display(person)
                    else:
                        name = None
                    if current == self.generations - 1:
                        parents = self._have_parents(person)
                    else:
                        parents = None
                    self.data[current][parent] = (name, person, parents, None, [])
                    if person is None:
                        # start,stop,male/right,state
                        self.angle[current][parent][2] = COLLAPSED
                    parent += 1

    def _have_parents(self, person):
        """
        Returns True if a person has parents. 
        TODO: is there no util function for this
        """
        if person:
            m = self._get_parent(person, False)
            f = self._get_parent(person, True)
            return not m is f is None
        return False

    def _get_parent(self, person, father):
        """
        Get the father of the family if father == True, otherwise mother
        """
        if person:
            parent_handle_list = person.get_parent_family_handle_list()
            if parent_handle_list:
                family_id = parent_handle_list[0]
                family = self.dbstate.db.get_family_from_handle(family_id)
                if family:
                    if father:
                        person_handle = Family.get_father_handle(family)
                    else:
                        person_handle = Family.get_mother_handle(family)
                    if person_handle:
                        return self.dbstate.db.get_person_from_handle(person_handle)
        return None

    def gen_pixels(self):
        """
        how many pixels a generation takes up in the fanchart
        """
        return PIXELS_PER_GENERATION

    def nrgen(self):
        #compute the number of generations present
        nrgen = None
        for generation in range(self.generations - 1, 0, -1):
            for p in range(len(self.data[generation])):
                (text, person, parents, child, userdata) = self.data[generation][p]
                if person:
                    nrgen = generation
                    break
            if nrgen is not None:
                break
        if nrgen is None:
            nrgen = 1
        return nrgen

    def halfdist(self):
        """
        Compute the half radius of the circle
        """
        nrgen = self.nrgen()
        return PIXELS_PER_GENERATION * nrgen + self.CENTER + BORDER_EDGE_WIDTH

    def get_radiusinout_for_generation(self,generation):
        outerradius=generation * PIXELS_PER_GENERATION + self.CENTER
        innerradius=(generation-1) * PIXELS_PER_GENERATION + self.CENTER
        if generation==0:
            innerradius= CHILDRING_WIDTH + TRANSLATE_PX
        return (innerradius,outerradius)
        
    def people_generator(self):
        """
        a generator over all people outside of the core person
        """
        for generation in range(self.generations):
            for p in range(len(self.data[generation])):
                (text, person, parents, child, userdata) = self.data[generation][p]
                yield (person, userdata)

    def innerpeople_generator(self):
        """
        a generator over all people inside of the core person
        """
        for childdata in self.childrenroot:
            (text, person, parents, child, userdata) = childdata
            yield (person, userdata)

    def on_draw(self, widget, cr, scale=1.):
        """
        The main method to do the drawing.
        If widget is given, we assume we draw in GTK3 and use the allocation. 
        To draw raw on the cairo context cr, set widget=None.
        """
        # first do size request of what we will need
        halfdist = self.halfdist()
        if widget:
            if self.form == FORM_CIRCLE:
                self.set_size_request(2 * halfdist, 2 * halfdist)
            elif self.form == FORM_HALFCIRCLE:
                self.set_size_request(2 * halfdist, halfdist + self.CENTER + PAD_PX)
            elif self.form == FORM_QUADRANT:
                self.set_size_request(halfdist + self.CENTER + PAD_PX, halfdist + self.CENTER + PAD_PX)

        cr.scale(scale, scale)
        if widget:
            self.center_xy = self.center_xy_from_delta()
        cr.translate(*self.center_xy)

        cr.save()
        cr.rotate(math.radians(self.rotate_value))
        for generation in range(self.generations - 1, 0, -1):
            for p in range(len(self.data[generation])):
                (text, person, parents, child, userdata) = self.data[generation][p]
                if person:
                    start, stop, state = self.angle[generation][p]
                    if state in [NORMAL, EXPANDED]:
                        self.draw_person(cr, person, start, stop, 
                                         generation, state, parents, child,
                                         userdata)
        cr.set_source_rgb(1, 1, 1) # white
        cr.move_to(0,0)
        cr.arc(0, 0, self.CENTER, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgb(0, 0, 0) # black
        cr.arc(0, 0, self.CENTER, 0, 2 * math.pi)
        cr.stroke()
        cr.restore()
        # Draw center person:
        (text, person, parents, child, userdata) = self.data[0][0]
        if person:
            self.draw_person(cr, person, math.pi/2, math.pi/2 + 2*math.pi,
                             0, NORMAL, parents, child,
                             userdata, is_central_person=True)
            #draw center disk to move chart
            cr.set_source_rgb(0, 0, 0) # black
            cr.move_to(TRANSLATE_PX, 0)
            cr.arc(0, 0, TRANSLATE_PX, 0, 2 * math.pi)
            if child: # has at least one child
                cr.fill()
            else:
                cr.stroke()
            if child and self.childring:
                self.draw_childring(cr)
        if self.background in [BACKGROUND_GRAD_AGE, BACKGROUND_GRAD_PERIOD]:
            self.draw_gradient_legend(cr, widget, halfdist)

    def draw_person(self, cr, person, start_rad, stop_rad, generation, 
                    state, parents, child, userdata, is_central_person=False):
        """
        Display the piece of pie for a given person. start_rad and stop_rad
        are in degrees. 
        """
        cr.save()
        r, g, b, a = self.background_box(person, generation, userdata)
        radiusin,radiusout = self.get_radiusinout_for_generation(generation)
        # If max generation, and they have parents:
        if generation == self.generations - 1 and parents:
            # draw an indicator
            color=(1.0, 1.0, 1.0, 1.0) # white
            self.draw_radbox(cr, radiusout, radiusout + BORDER_EDGE_WIDTH, start_rad, stop_rad, color, thick=False)
        # now draw the person
        color=(r/255., g/255., b/255., a)
        thick=(state == EXPANDED)
        if not is_central_person:
            self.draw_radbox(cr, radiusin, radiusout, start_rad, stop_rad, color, thick)
        else:
            #special box for centrer pers
            cr.arc(0, 0, radiusout, 0, 2 * math.pi)
            if self.childring and len(self.childrenroot)>0:
                cr.arc_negative(0, 0, radiusin, 2 * math.pi, 0)
                cr.close_path()
            cr.set_source_rgba(*color)
            cr.fill()

        if self.last_x is None or self.last_y is None: 
            #we are not in a move, so draw text
            radial = False
            if self.radialtext: ## and generation >= 6:
                space_arc_text =  (radiusin+radiusout)/2 * (stop_rad-start_rad)
                # is there more space to print it radial ?
                radial= (space_arc_text < (radiusout-radiusin) * 1.1)
            self.draw_person_text(cr, person, radiusin, radiusout, start_rad, stop_rad, 
                           radial, self.fontcolor(r, g, b, a), self.fontbold(a))
        cr.restore()

    def draw_childring(self, cr):
        cr.move_to(TRANSLATE_PX + CHILDRING_WIDTH, 0)
        cr.set_source_rgb(0, 0, 0) # black
        cr.set_line_width(1)
        cr.arc(0, 0, TRANSLATE_PX + CHILDRING_WIDTH, 0, 2 * math.pi)
        cr.stroke()
        nrchild = len(self.childrenroot)
        #Y axis is downward. positve angles are hence clockwise
        startangle = math.pi
        if nrchild <= 4:
            angleinc = math.pi/2
        else:
            angleinc = 2 * math.pi / nrchild
        for childdata in self.childrenroot:
            (text, person, parents, child, userdata) = childdata
            self.draw_innerring(cr, person, userdata, startangle, angleinc)
            startangle += angleinc

    def expand_parents(self, generation, selected, current):
        if generation >= self.generations: return
        selected = 2 * selected
        start, stop, state = self.angle[generation][selected]
        if state in [NORMAL, EXPANDED]:
            slice = (stop - start) * 2.0
            self.angle[generation][selected] = [current, current+slice, state]
            self.expand_parents(generation + 1, selected, current)
            current += slice
        start, stop, state = self.angle[generation][selected+1]
        if state in [NORMAL, EXPANDED]:
            slice = (stop - start) * 2.0
            self.angle[generation][selected+1] = [current,current+slice,
                                                  state]
            self.expand_parents(generation + 1, selected+1, current)

    def show_parents(self, generation, selected, angle, slice):
        if generation >= self.generations: return
        selected *= 2
        self.angle[generation][selected][0] = angle
        self.angle[generation][selected][1] = angle + slice
        self.angle[generation][selected][2] = NORMAL
        self.show_parents(generation+1, selected, angle, slice/2.0)
        self.angle[generation][selected+1][0] = angle + slice
        self.angle[generation][selected+1][1] = angle + slice + slice
        self.angle[generation][selected+1][2] = NORMAL
        self.show_parents(generation+1, selected + 1, angle + slice, slice/2.0)

    def hide_parents(self, generation, selected, angle):
        if generation >= self.generations: return
        selected = 2 * selected
        self.angle[generation][selected][0] = angle
        self.angle[generation][selected][1] = angle
        self.angle[generation][selected][2] = COLLAPSED
        self.hide_parents(generation + 1, selected, angle)
        self.angle[generation][selected+1][0] = angle
        self.angle[generation][selected+1][1] = angle
        self.angle[generation][selected+1][2] = COLLAPSED
        self.hide_parents(generation + 1, selected+1, angle)

    def shrink_parents(self, generation, selected, current):
        if generation >= self.generations: return
        selected = 2 * selected
        start, stop, state = self.angle[generation][selected]
        if state in [NORMAL, EXPANDED]:
            slice = (stop - start) / 2.0
            self.angle[generation][selected] = [current, current + slice, 
                                                state]
            self.shrink_parents(generation + 1, selected, current)
            current += slice
        start, stop, state = self.angle[generation][selected+1]
        if state in [NORMAL, EXPANDED]:
            slice = (stop - start) / 2.0
            self.angle[generation][selected+1] = [current, current+slice,
                                                  state]
            self.shrink_parents(generation + 1, selected+1, current)

    def change_slice(self, generation, selected):
        if generation < 1:
            return
        gstart, gstop, gstate = self.angle[generation][selected]
        if gstate == NORMAL: # let's expand
            if selected % 2 == 0:
                # go to right
                stop = gstop + (gstop - gstart)
                self.angle[generation][selected] = [gstart, stop, EXPANDED]
                self.expand_parents(generation + 1, selected, gstart)
                start, stop, state = self.angle[generation][selected+1]
                self.angle[generation][selected+1] = [stop, stop, COLLAPSED]
                self.hide_parents(generation+1, selected+1, stop)
            else:
                # go to left
                start = gstart - (gstop - gstart)
                self.angle[generation][selected] = [start, gstop, EXPANDED]
                self.expand_parents(generation + 1, selected, start)
                start, stop, state = self.angle[generation][selected-1]
                self.angle[generation][selected-1] = [start, start, COLLAPSED]
                self.hide_parents(generation+1, selected-1, start)
        elif gstate == EXPANDED: # let's shrink
            if selected % 2 == 0:
                # shrink from right
                slice = (gstop - gstart)/2.0
                stop = gstop - slice
                self.angle[generation][selected] = [gstart, stop, NORMAL]
                self.shrink_parents(generation+1, selected, gstart)
                self.angle[generation][selected+1][0] = stop # start
                self.angle[generation][selected+1][1] = stop + slice # stop
                self.angle[generation][selected+1][2] = NORMAL
                self.show_parents(generation+1, selected+1, stop, slice/2.0)
            else:
                # shrink from left
                slice = (gstop - gstart)/2.0
                start = gstop - slice
                self.angle[generation][selected] = [start, gstop, NORMAL]
                self.shrink_parents(generation+1, selected, start)
                start, stop, state = self.angle[generation][selected-1]
                self.angle[generation][selected-1] = [start, start+slice,
                                                      NORMAL]
                self.show_parents(generation+1, selected-1, start, slice/2.0)

    def person_under_cursor(self, curx, cury):
        """
        Determine the generation and the position in the generation at 
        position x and y, as well as the type of box. 
        generation = -1 on center black dot
        generation >= self.generations outside of diagram
        """
        # compute angle, radius, find out who would be there (rotated)

        # center coordinate
        fanxy = curx - self.center_xy[0], cury - self.center_xy[1]
        radius = math.sqrt((fanxy[0]) ** 2 + (fanxy[1]) ** 2)
        if radius < TRANSLATE_PX:
            generation = -1
        elif (self.childring and self.angle[-2] and 
                    radius < TRANSLATE_PX + CHILDRING_WIDTH):
            generation = -2  # indication of one of the children
        elif radius < self.CENTER:
            generation = 0
        else:
            generation = self.generations
            for gen in range(self.generations-1):
                radiusin,radiusout = self.get_radiusinout_for_generation(gen)
                if radiusin <= radius <= radiusout:
                    generation = gen
                    break
        btype = self.boxtype(radius)

        rads = math.atan2( fanxy[1], fanxy[0])
        if rads < 0: # second half of unit circle
            rads = math.pi + (math.pi + rads)
        #angle before rotation:
        pos = ((rads/(math.pi * 2) - self.rotate_value/360.) * 360.0) % 360
        #children are in cairo angle (clockwise) from pi to 3 pi
        #rads however is clock 0 to 2 pi
        if rads < math.pi:
            rads += 2 * math.pi
        # if generation is in expand zone:
        # FIXME: add a way of expanding 
        # find what person is in this position:
        selected = None
        if (0 <= generation < self.generations):
            selected = self.personpos_at_angle(generation, pos, btype)
        elif generation == -2:
            for p in range(len(self.angle[generation])):
                start, stop, state = self.angle[generation][p]
                if start <= rads <= stop:
                    selected = p
                    break
            
        return generation, selected, btype
    def personpos_at_angle(self, generation, angledeg, btype):
        """
        returns the person in generation generation at angle.
        """
        if generation == 0:
            return 0
        selected = None
        for p in range(len(self.angle[generation])):
            if self.data[generation][p][1]: # there is a person there
                start, stop, state = self.angle[generation][p]
                if state == COLLAPSED: continue
                if start <= angledeg <= stop:
                    selected = p
                    break
        return selected

    def person_at(self, generation, pos, btype):
        """
        returns the person at generation, pos, btype
        """
        if pos is None:
            return None
        if generation == -2:
            person = self.childrenroot[pos][1]
        else:
            person = self.data[generation][pos][1]
        return person

    def family_at(self, generation, pos, btype):
        """
        returns the family at generation, pos, btype
        Difficult here, we would need to go to child, and then obtain the first
        parent family, as that is the family that is shown.
        """
        return None

    def do_mouse_click(self):
        # no drag occured, expand or collapse the section
        self.change_slice(self._mouse_click_gen, self._mouse_click_sel)
        self._mouse_click = False
        self.queue_draw()

class FanChartGrampsGUI(object):
    """ class for functions fanchart GUI elements will need in Gramps
    """
    def __init__(self, on_childmenu_changed):
        """
        Common part of GUI that shows Fan Chart, needs to know what to do if
        one moves via Fan Ch    def set_fan(self, fan):art to a new person
        on_childmenu_changed: in popup, function called on moving to a new person
        """
        self.fan = None
        self.on_childmenu_changed = on_childmenu_changed
        self.format_helper = FormattingHelper(self.dbstate)
    
    def set_fan(self, fan):
        """
        Set the fanchartwidget to work on
        """
        self.fan = fan
        self.fan.format_helper = self.format_helper
        self.fan.goto = self.on_childmenu_changed

    def main(self):
        """
        Fill the data structures with the active data. This initializes all 
        data.
        """
        root_person_handle = self.get_active('Person')
        self.fan.set_values(root_person_handle, self.maxgen, self.background,
                        self.childring, self.flipupsidedownname, self.twolinename, self.radialtext, self.fonttype,
                        self.grad_start, self.grad_end,
                        self.generic_filter, self.alpha_filter, self.form)
        self.fan.reset()
        self.fan.queue_draw()

    def on_popup(self, obj, event, person_handle, family_handle=None):
        """
        Builds the full menu (including Siblings, Spouses, Children, 
        and Parents) with navigation.
        """
        #store menu for GTK3 to avoid it being destroyed before showing
        self.menu = Gtk.Menu()
        menu = self.menu
        menu.set_title(_('People Menu'))

        person = self.dbstate.db.get_person_from_handle(person_handle)
        if not person:
            return 0

        go_image = Gtk.Image.new_from_stock(Gtk.STOCK_JUMP_TO,Gtk.IconSize.MENU)
        go_image.show()
        go_item = Gtk.ImageMenuItem(name_displayer.display(person))
        go_item.set_image(go_image)
        go_item.connect("activate", self.on_childmenu_changed, person_handle)
        go_item.show()
        menu.append(go_item)

        edit_item = Gtk.ImageMenuItem.new_from_stock(stock_id=Gtk.STOCK_EDIT,
                                                     accel_group=None)
        edit_item.connect("activate", self.edit_person_cb, person_handle)
        edit_item.show()
        menu.append(edit_item)
        if family_handle:
            family = self.dbstate.db.get_family_from_handle(family_handle)
            edit_fam_item = Gtk.ImageMenuItem.new_from_stock(
                                    stock_id=Gtk.STOCK_EDIT, accel_group=None)
            edit_fam_item.set_label(_("Edit family"))
            edit_fam_item.connect("activate", self.edit_fam_cb, family_handle)
            edit_fam_item.show()
            menu.append(edit_fam_item)
            #see if a reorder button is needed
            if family.get_father_handle() == person_handle:
                parth = family.get_mother_handle()
            else:
                parth = family.get_father_handle()
            lenfams = 0
            if parth:
                partner = self.dbstate.db.get_person_from_handle(parth)
                lenfams = len(partner.get_family_handle_list())
                if lenfams in [0, 1]:
                    lenfams = len(partner.get_parent_family_handle_list())
            reord_fam_item = Gtk.ImageMenuItem.new_from_stock(
                           stock_id=Gtk.STOCK_SORT_ASCENDING, accel_group=None)
            reord_fam_item.set_label(_("Reorder families"))
            reord_fam_item.connect("activate", self.reord_fam_cb, parth)
            reord_fam_item.set_sensitive(lenfams > 1)
            reord_fam_item.show()
            menu.append(reord_fam_item)

        clipboard_item = Gtk.ImageMenuItem.new_from_stock(stock_id=Gtk.STOCK_COPY, accel_group=None)
        clipboard_item.connect("activate", self.copy_person_to_clipboard_cb,
                               person_handle)
        clipboard_item.show()
        menu.append(clipboard_item)

        # collect all spouses, parents and children
        linked_persons = []
        
        # Go over spouses and build their menu
        item = Gtk.MenuItem(label=_("Spouses"))
        fam_list = person.get_family_handle_list()
        no_spouses = 1
        for fam_id in fam_list:
            family = self.dbstate.db.get_family_from_handle(fam_id)
            if family.get_father_handle() == person.get_handle():
                sp_id = family.get_mother_handle()
            else:
                sp_id = family.get_father_handle()
            spouse = self.dbstate.db.get_person_from_handle(sp_id)
            if not spouse:
                continue

            if no_spouses:
                no_spouses = 0
                item.set_submenu(Gtk.Menu())
                sp_menu = item.get_submenu()

            go_image = Gtk.Image.new_from_stock(Gtk.STOCK_JUMP_TO, Gtk.IconSize.MENU)
            go_image.show()
            sp_item = Gtk.ImageMenuItem(name_displayer.display(spouse))
            sp_item.set_image(go_image)
            linked_persons.append(sp_id)
            sp_item.connect("activate", self.on_childmenu_changed, sp_id)
            sp_item.show()
            sp_menu.append(sp_item)

        if no_spouses:
            item.set_sensitive(0)

        item.show()
        menu.append(item)
        
        # Go over siblings and build their menu
        item = Gtk.MenuItem(label=_("Siblings"))
        pfam_list = person.get_parent_family_handle_list()
        no_siblings = 1
        for f in pfam_list:
            fam = self.dbstate.db.get_family_from_handle(f)
            sib_list = fam.get_child_ref_list()
            for sib_ref in sib_list:
                sib_id = sib_ref.ref
                if sib_id == person.get_handle():
                    continue
                sib = self.dbstate.db.get_person_from_handle(sib_id)
                if not sib:
                    continue

                if no_siblings:
                    no_siblings = 0
                    item.set_submenu(Gtk.Menu())
                    sib_menu = item.get_submenu()

                if find_children(self.dbstate.db,sib):
                    label = Gtk.Label(label='<b><i>%s</i></b>' % escape(name_displayer.display(sib)))
                else:
                    label = Gtk.Label(label=escape(name_displayer.display(sib)))

                go_image = Gtk.Image.new_from_stock(Gtk.STOCK_JUMP_TO, Gtk.IconSize.MENU)
                go_image.show()
                sib_item = Gtk.ImageMenuItem(None)
                sib_item.set_image(go_image)
                label.set_use_markup(True)
                label.show()
                label.set_alignment(0,0)
                sib_item.add(label)
                linked_persons.append(sib_id)
                sib_item.connect("activate", self.on_childmenu_changed, sib_id)
                sib_item.show()
                sib_menu.append(sib_item)

        if no_siblings:
            item.set_sensitive(0)
        item.show()
        menu.append(item)
        
        # Go over children and build their menu
        item = Gtk.MenuItem(label=_("Children"))
        no_children = 1
        childlist = find_children(self.dbstate.db, person)
        for child_handle in childlist:
            child = self.dbstate.db.get_person_from_handle(child_handle)
            if not child:
                continue
        
            if no_children:
                no_children = 0
                item.set_submenu(Gtk.Menu())
                child_menu = item.get_submenu()

            if find_children(self.dbstate.db,child):
                label = Gtk.Label(label='<b><i>%s</i></b>' % escape(name_displayer.display(child)))
            else:
                label = Gtk.Label(label=escape(name_displayer.display(child)))

            go_image = Gtk.Image.new_from_stock(Gtk.STOCK_JUMP_TO, Gtk.IconSize.MENU)
            go_image.show()
            child_item = Gtk.ImageMenuItem(None)
            child_item.set_image(go_image)
            label.set_use_markup(True)
            label.show()
            label.set_alignment(0,0)
            child_item.add(label)
            linked_persons.append(child_handle)
            child_item.connect("activate", self.on_childmenu_changed, child_handle)
            child_item.show()
            child_menu.append(child_item)

        if no_children:
            item.set_sensitive(0)
        item.show()
        menu.append(item)

        # Go over parents and build their menu
        item = Gtk.MenuItem(label=_("Parents"))
        item.set_submenu(Gtk.Menu())
        par_menu = item.get_submenu()
        no_parents = 1
        par_list = find_parents(self.dbstate.db,person)
        for par_id in par_list:
            par = self.dbstate.db.get_person_from_handle(par_id)
            if not par:
                continue

            if no_parents:
                no_parents = 0

            if find_parents(self.dbstate.db,par):
                label = Gtk.Label(label='<b><i>%s</i></b>' % escape(name_displayer.display(par)))
            else:
                label = Gtk.Label(label=escape(name_displayer.display(par)))

            go_image = Gtk.Image.new_from_stock(Gtk.STOCK_JUMP_TO, Gtk.IconSize.MENU)
            go_image.show()
            par_item = Gtk.ImageMenuItem(None)
            par_item.set_image(go_image)
            label.set_use_markup(True)
            label.show()
            label.set_alignment(0,0)
            par_item.add(label)
            linked_persons.append(par_id)
            par_item.connect("activate", self.on_childmenu_changed, par_id)
            par_item.show()
            par_menu.append(par_item)

        if no_parents:
            #show an add button
            add_item = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_ADD, None)
            add_item.connect("activate", self.on_add_parents, person_handle)
            add_item.show()
            par_menu.append(add_item)
            
        item.show()
        menu.append(item)
    
        # Go over parents and build their menu
        item = Gtk.MenuItem(label=_("Related"))
        no_related = 1
        for p_id in find_witnessed_people(self.dbstate.db,person):
            #if p_id in linked_persons:
            #    continue    # skip already listed family members
            
            per = self.dbstate.db.get_person_from_handle(p_id)
            if not per:
                continue

            if no_related:
                no_related = 0
                item.set_submenu(Gtk.Menu())
                per_menu = item.get_submenu()

            label = Gtk.Label(label=escape(name_displayer.display(per)))

            go_image = Gtk.Image.new_from_stock(Gtk.STOCK_JUMP_TO, Gtk.IconSize.MENU)
            go_image.show()
            per_item = Gtk.ImageMenuItem(None)
            per_item.set_image(go_image)
            label.set_use_markup(True)
            label.show()
            label.set_alignment(0, 0)
            per_item.add(label)
            per_item.connect("activate", self.on_childmenu_changed, p_id)
            per_item.show()
            per_menu.append(per_item)
        
        if no_related:
            item.set_sensitive(0)
        item.show()
        menu.append(item)
        
        #we now construct an add menu
        item = Gtk.MenuItem(label=_("Add"))
        item.set_submenu(Gtk.Menu())
        add_menu = item.get_submenu()
        if family_handle:
            # allow to add a child to this family
            add_child_item = Gtk.ImageMenuItem.new_from_stock(
                                    stock_id=Gtk.STOCK_ADD, accel_group=None)
            add_child_item.set_label(_("Add child to family"))
            add_child_item.connect("activate", self.add_child_to_fam_cb,
                                   family_handle)
            add_child_item.show()
            add_menu.append(add_child_item)
        elif person_handle:
            #allow to add a partner to this person
            add_partner_item = Gtk.ImageMenuItem.new_from_stock(
                                    stock_id=Gtk.STOCK_ADD, accel_group=None)
            add_partner_item.set_label(_("Add partner to person"))
            add_partner_item.connect("activate", self.add_partner_to_pers_cb,
                                   person_handle)
            add_partner_item.show()
            add_menu.append(add_partner_item)
            
        add_pers_item = Gtk.ImageMenuItem.new_from_stock(stock_id=Gtk.STOCK_ADD,
                                                         accel_group=None)
        add_pers_item.set_label(_("Add a person"))
        add_pers_item.connect("activate", self.add_person_cb)
        add_pers_item.show()
        add_menu.append(add_pers_item)
        item.show()
        menu.append(item)
        
        menu.popup(None, None, None, None, event.button, event.time)
        return 1
    
    def edit_person_cb(self, obj, person_handle):
        person = self.dbstate.db.get_person_from_handle(person_handle)
        if person:
            try:
                EditPerson(self.dbstate, self.uistate, [], person)
            except WindowActiveError:
                pass
            return True
        return False

    def edit_fam_cb(self, obj, family_handle):
        fam = self.dbstate.db.get_family_from_handle(family_handle)
        if fam:
            try:
                EditFamily(self.dbstate, self.uistate, [], fam)
            except WindowActiveError:
                pass
            return True
        return False

    def reord_fam_cb(self, obj, person_handle):
        try:
            Reorder(self.dbstate, self.uistate, [], person_handle)
        except WindowActiveError:
            pass
        return True

    def add_person_cb(self, obj):
        """
        Add a person
        """
        person = Person()
        #the editor requires a surname
        person.primary_name.add_surname(Surname())
        person.primary_name.set_primary_surname(0)
        try:
            EditPerson(self.dbstate, self.uistate, [], person)
        except WindowActiveError:
            pass

    def add_child_to_fam_cb(self, obj, family_handle):
        callback = lambda x: self.callback_add_child(x, family_handle)
        person = Person()
        name = Name()
        #the editor requires a surname
        name.add_surname(Surname())
        name.set_primary_surname(0)
        family = self.dbstate.db.get_family_from_handle(family_handle)
        father = self.dbstate.db.get_person_from_handle(
                                    family.get_father_handle())
        if father:
            preset_name(father, name)
        person.set_primary_name(name)
        try:
            EditPerson(self.dbstate, self.uistate, [], person, 
                       callback=callback)
        except WindowActiveError:
            pass
   
    def callback_add_child(self, person, family_handle):
        ref = ChildRef()
        ref.ref = person.get_handle()
        family = self.dbstate.db.get_family_from_handle(family_handle)
        family.add_child_ref(ref)
        
        with DbTxn(_("Add Child to Family"), self.dbstate.db) as trans:
            #add parentref to child
            person.add_parent_family_handle(family_handle)
            #default relationship is used
            self.dbstate.db.commit_person(person, trans)
            #add child to family
            self.dbstate.db.commit_family(family, trans)

    def add_partner_to_pers_cb(self, obj, person_handle):
        """
        Add a family with the person preset
        """
        family = Family()
        person = self.dbstate.db.get_person_from_handle(person_handle)

        if not person:
            return
            
        if person.gender == Person.MALE:
            family.set_father_handle(person.handle)
        else:
            family.set_mother_handle(person.handle)

        try:
            EditFamily(self.dbstate, self.uistate, [], family)
        except WindowActiveError:
            pass

    def on_add_parents(self, obj, person_handle):
        family = Family()
        childref = ChildRef()
        childref.set_reference_handle(person_handle)
        family.add_child_ref(childref)
        try:
            EditFamily(self.dbstate, self.uistate, [], family)
        except WindowActiveError:
            return

    def copy_person_to_clipboard_cb(self, obj,person_handle):
        """Renders the person data into some lines of text and puts that into the clipboard"""
        person = self.dbstate.db.get_person_from_handle(person_handle)
        if person:
            cb = Gtk.Clipboard.get_for_display(Gdk.Display.get_default(), 
                        Gdk.SELECTION_CLIPBOARD)
            cb.set_text( self.format_helper.format_person(person,11), -1)
            return True
        return False
