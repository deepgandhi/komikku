# Copyright (C) 2019-2020 Valéry Febvre
# SPDX-License-Identifier: GPL-3.0-only or GPL-3.0-or-later
# Author: Valéry Febvre <vfebvre@easter-eggs.com>

from gi.repository import Gdk
from gi.repository import Gio
from gi.repository import GLib
from gi.repository import Gtk

from komikku.models import Settings
from komikku.reader.controls import Controls
from komikku.reader.pager import Pager
import shutil
import os
import magic

class Reader:
    manga = None
    chapters_consulted = None
    chapter=None

    def __init__(self, window):
        self.window = window
        self.builder = window.builder
        self.builder.add_from_resource('/info/febvre/Komikku/ui/menu/reader.xml')

        self.overlay = self.builder.get_object('reader_overlay')

        # Headerbar
        self.title_label = self.builder.get_object('reader_page_title_label')
        self.subtitle_label = self.builder.get_object('reader_page_subtitle_label')

        # Pager
        self.pager = Pager(self)
        self.overlay.add(self.pager)

        # Page number indicator
        self.page_number_label = Gtk.Label()
        self.page_number_label.get_style_context().add_class('reader-page-number-indicator-label')
        self.page_number_label.set_valign(Gtk.Align.END)
        self.overlay.add_overlay(self.page_number_label)

        # Controls
        self.controls = Controls(self)

    @property
    def background_color(self):
        return self.manga.background_color or Settings.get_default().background_color

    @property
    def borders_crop(self):
        if self.manga.borders_crop in (0, 1):
            return bool(self.manga.borders_crop)

        return Settings.get_default().borders_crop

    @property
    def reading_direction(self):
        return self.manga.reading_direction or Settings.get_default().reading_direction

    @property
    def scaling(self):
        return self.manga.scaling or Settings.get_default().scaling

    @property
    def size(self):
        return self.window.get_size()

    def add_accelerators(self):
        self.window.application.set_accels_for_action('app.reader.take_screenshot',['<Control>s'])

    def add_actions(self):
        # Screenshot
        self.take_screenshot=Gio.SimpleAction.new('reader.take_screenshot',None)
        self.take_screenshot.connect('activate',self.screenshot_taken)

        # Reading direction
        self.reading_direction_action = Gio.SimpleAction.new_stateful(
            'reader.reading-direction', GLib.VariantType.new('s'), GLib.Variant('s', 'right-to-left'))
        self.reading_direction_action.connect('change-state', self.on_reading_direction_changed)

        # Scaling
        self.scaling_action = Gio.SimpleAction.new_stateful(
            'reader.scaling', GLib.VariantType.new('s'), GLib.Variant('s', 'screen'))
        self.scaling_action.connect('change-state', self.on_scaling_changed)

        # Background color
        self.background_color_action = Gio.SimpleAction.new_stateful(
            'reader.background-color', GLib.VariantType.new('s'), GLib.Variant('s', 'white'))
        self.background_color_action.connect('change-state', self.on_background_color_changed)

        # Borders crop
        self.borders_crop_action = Gio.SimpleAction.new_stateful('reader.borders-crop', None, GLib.Variant('b', False))
        self.borders_crop_action.connect('change-state', self.on_borders_crop_changed)

        self.window.application.add_action(self.reading_direction_action)
        self.window.application.add_action(self.scaling_action)
        self.window.application.add_action(self.background_color_action)
        self.window.application.add_action(self.borders_crop_action)
        self.window.application.add_action(self.take_screenshot)

    def init(self, manga, chapter):
        self.manga = manga
        self.chapter=chapter

        # Reset list of chapters consulted
        self.chapters_consulted = set()

        # Init settings
        self.set_reading_direction()
        self.set_scaling()
        self.set_background_color()
        self.set_borders_crop()

        self.show()

        self.pager.init(chapter)


    def on_background_color_changed(self, action, variant):
        value = variant.get_string()
        if value == self.manga.background_color:
            return

        self.manga.update(dict(background_color=value))
        self.set_background_color()

    def on_borders_crop_changed(self, action, variant):
        self.manga.update(dict(borders_crop=variant.get_boolean()))
        self.set_borders_crop()
        self.pager.crop_pages_borders()

    def on_reading_direction_changed(self, action, variant):
        value = variant.get_string()
        if value == self.manga.reading_direction:
            return

        # Reverse pages order
        # except in cases: LTR => Vertical and Vertical => LTR
        if value not in ('left-to-right', 'vertical') or self.manga.reading_direction not in ('left-to-right', 'vertical'):
            self.pager.reverse_pages()

        self.manga.update(dict(reading_direction=value))
        self.set_reading_direction()

        self.pager.set_orientation()

    def on_resize(self):
        self.pager.resize_pages()

    def on_scaling_changed(self, action, variant):
        value = variant.get_string()
        if value == self.manga.scaling:
            return

        self.manga.update(dict(scaling=value))
        self.set_scaling()

        self.pager.rescale_pages()

    def set_background_color(self):
        self.background_color_action.set_state(GLib.Variant('s', self.background_color))
        if self.background_color == 'white':
            self.pager.viewport.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
        else:
            self.pager.viewport.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0, 0, 0, 1))

    def set_borders_crop(self):
        self.borders_crop_action.set_state(GLib.Variant('b', self.borders_crop))

    def set_reading_direction(self):
        self.reading_direction_action.set_state(GLib.Variant('s', self.reading_direction))
        self.controls.set_scale_direction(self.reading_direction == 'right-to-left')

    def set_scaling(self):
        self.scaling_action.set_state(GLib.Variant('s', self.scaling))

    def show(self):
        def on_menu_popover_closed(menu_button):
            self.pager.grab_focus()

        self.builder.get_object('fullscreen_button').show()

        self.window.menu_button.set_menu_model(self.builder.get_object('menu-reader'))
        self.window.menu_button_image.set_from_icon_name('view-more-symbolic', Gtk.IconSize.MENU)
        # Watch when menu is closed to be able to restore focus to pager
        self.window.menu_button.get_popover().connect('closed', on_menu_popover_closed)

        self.page_number_label.hide()
        self.controls.hide()

        if Settings.get_default().fullscreen:
            self.window.set_fullscreen()

        self.window.show_page('reader')

    def toggle_controls(self):
        if self.controls.is_visible:
            self.controls.hide()
            self.page_number_label.show()
        else:
            self.controls.show()
            self.page_number_label.hide()

    def update_page_number(self, number, total):
        self.page_number_label.set_text('{0}/{1}'.format(number, total))

        if not self.controls.is_visible:
            self.page_number_label.show()

    def update_title(self, chapter):
        # Add chapter to list of chapters consulted
        # This list is used by the Card page to update chapters rows
        self.chapters_consulted.add(chapter)

        # Set title & subtitle (headerbar)
        self.title_label.set_text(chapter.manga.name)
        subtitle = chapter.title
        if chapter.manga.name in subtitle:
            subtitle = subtitle.replace(chapter.manga.name, '').strip()
        self.subtitle_label.set_text(subtitle)
       
    def screenshot_taken(self,action,param):
    	print(self.chapter.title)
    	page=self.pager.current_page
        page_name=str(page.index+1)
        chapter_name="chapter_"+str(self.chapter.id)
        manga_name=self.manga.name
        original=page.path
        filetype=magic.from_file(original,mime=True).split("/")[-1]
        filename="_".join([manga_name,chapter_name,page_name])
        destination=os.getenv("HOME")+"/Pictures/Komikku/"
        if not os.path.exists(destination):
            os.mkdir(destination)
        shutil.copy(original,destination+filename+"."+filetype)

