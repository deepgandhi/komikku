# Copyright (C) 2019-2020 Valéry Febvre
# SPDX-License-Identifier: GPL-3.0-only or GPL-3.0-or-later
# Author: Valéry Febvre <vfebvre@easter-eggs.com>

from copy import deepcopy
from gettext import gettext as _
import threading
import time

from gi.repository import Gdk
from gi.repository import Gio
from gi.repository import GLib
from gi.repository import Gtk
from gi.repository.GdkPixbuf import Pixbuf
from gi.repository.GdkPixbuf import PixbufAnimation

from komikku.models import create_db_connection
from komikku.models import Download
from komikku.models import update_rows
from komikku.servers import get_file_mime_type
from komikku.utils import folder_size
from komikku.utils import html_escape
from komikku.utils import scale_pixbuf_animation


class Card:
    manga = None
    selection_mode = False

    def __init__(self, window):
        self.window = window
        self.builder = window.builder
        self.builder.add_from_resource('/info/febvre/Komikku/ui/menu/card.xml')
        self.builder.add_from_resource('/info/febvre/Komikku/ui/menu/card_selection_mode.xml')

        self.title_label = self.builder.get_object('card_page_title_label')

        self.stack = self.builder.get_object('card_stack')
        self.info_grid = InfoGrid(self)
        self.chapters_list = ChaptersList(self)

        self.window.updater.connect('manga-updated', self.on_manga_updated)

    @property
    def sort_order(self):
        return self.manga.sort_order or 'desc'

    def add_actions(self):
        delete_action = Gio.SimpleAction.new('card.delete', None)
        delete_action.connect('activate', self.on_delete_menu_clicked)
        self.window.application.add_action(delete_action)

        update_action = Gio.SimpleAction.new('card.update', None)
        update_action.connect('activate', self.on_update_menu_clicked)
        self.window.application.add_action(update_action)

        self.sort_order_action = Gio.SimpleAction.new_stateful('card.sort-order', GLib.VariantType.new('s'), GLib.Variant('s', 'desc'))
        self.sort_order_action.connect('change-state', self.on_sort_order_changed)
        self.window.application.add_action(self.sort_order_action)

        open_in_browser_action = Gio.SimpleAction.new('card.open-in-browser', None)
        open_in_browser_action.connect('activate', self.on_open_in_browser_menu_clicked)
        self.window.application.add_action(open_in_browser_action)

        self.chapters_list.add_actions()

    def enter_selection_mode(self):
        self.selection_mode = True

        self.chapters_list.enter_selection_mode()

        self.window.titlebar.set_selection_mode(True)
        self.window.menu_button.set_menu_model(self.builder.get_object('menu-card-selection-mode'))

    def init(self, manga, transition=True):
        # Default page is Chapters page
        self.stack.set_visible_child_name('page_card_chapters')

        self.manga = manga
        # Unref chapters to force a reload
        self.manga._chapters = None

        if manga.server.status == 'disabled':
            self.window.show_notification(
                _('NOTICE\n{0} server is not longer supported.\nPlease switch to another server.').format(manga.server.name)
            )

        if len(manga.chapters) > 0:
            self.window.activity_indicator.start()

        self.chapters_list.clear()

        self.show(transition)

        self.populate()

    def leave_selection_mode(self):
        self.selection_mode = False

        self.chapters_list.leave_selection_mode()

        self.window.titlebar.set_selection_mode(False)
        self.window.menu_button.set_menu_model(self.builder.get_object('menu-card'))

    def on_delete_menu_clicked(self, action, param):
        def confirm_callback():
            # Stop Downloader & Updater
            self.window.downloader.stop()
            self.window.updater.stop()

            while self.window.downloader.running or self.window.updater.running:
                time.sleep(0.1)
                continue

            # Safely delete manga in DB
            self.manga.delete()

            # Restart Downloader & Updater
            self.window.downloader.start()
            self.window.updater.start()

            # Finally, update and show library
            db_conn = create_db_connection()
            nb_mangas = db_conn.execute('SELECT count(*) FROM mangas').fetchone()[0]
            db_conn.close()

            if nb_mangas == 0:
                # Library is now empty
                self.window.library.populate()
            else:
                self.window.library.on_manga_deleted(self.manga)

            self.window.library.show()

        self.window.confirm(
            _('Delete?'),
            _('Are you sure you want to delete this manga?'),
            confirm_callback
        )

    def on_manga_updated(self, updater, manga, nb_recent_chapters, nb_deleted_chapters):
        if self.window.page == 'card' and self.manga.id == manga.id:
            self.manga = manga

            if nb_recent_chapters > 0 or nb_deleted_chapters > 0:
                if len(manga.chapters) > 0:
                    self.window.activity_indicator.start()

                self.chapters_list.clear()
                self.chapters_list.populate()

            self.info_grid.populate()

    def on_open_in_browser_menu_clicked(self, action, param):
        Gtk.show_uri_on_window(None, self.manga.server.get_manga_url(self.manga.slug, self.manga.url), time.time())

    def on_sort_order_changed(self, action, variant):
        value = variant.get_string()
        if value == self.manga.sort_order:
            return

        self.manga.update(dict(sort_order=value))
        self.set_sort_order()

    def on_update_menu_clicked(self, action, param):
        self.window.updater.add(self.manga)
        self.window.updater.start()

    def populate(self):
        self.chapters_list.populate()
        self.info_grid.populate()

        self.set_sort_order(invalidate=False)

    def set_sort_order(self, invalidate=True):
        self.sort_order_action.set_state(GLib.Variant('s', self.sort_order))
        if invalidate:
            self.chapters_list.listbox.invalidate_sort()

    def show(self, transition=True):
        self.title_label.set_text(self.manga.name)

        self.window.left_button_image.set_from_icon_name('go-previous-symbolic', Gtk.IconSize.MENU)

        self.builder.get_object('fullscreen_button').hide()

        self.window.menu_button.set_menu_model(self.builder.get_object('menu-card'))
        self.window.menu_button_image.set_from_icon_name('view-more-symbolic', Gtk.IconSize.MENU)

        self.window.show_page('card', transition=transition)

    def refresh(self, chapters):
        self.info_grid.refresh()
        self.chapters_list.refresh(chapters)


class ChaptersList:
    populate_countdown = 0
    selection_mode_count = 0
    selection_mode_range = False
    selection_mode_last_row_index = None

    def __init__(self, card):
        self.card = card

        self.listbox = self.card.builder.get_object('chapters_listbox')
        self.listbox.get_style_context().add_class('list-bordered')
        self.listbox.connect('row-activated', self.on_chapter_row_clicked)

        self.gesture = Gtk.GestureLongPress.new(self.listbox)
        self.gesture.set_touch_only(False)
        self.gesture.connect('pressed', self.on_gesture_long_press_activated)

        self.card.window.downloader.connect('download-changed', self.update_chapter_row)

        def sort(child1, child2):
            """
            This function gets two children and has to return:
            - a negative integer if the firstone should come before the second one
            - zero if they are equal
            - a positive integer if the second one should come before the firstone
            """
            if child1.chapter.rank > child2.chapter.rank:
                return -1 if self.card.sort_order == 'desc' else 1

            if child1.chapter.rank < child2.chapter.rank:
                return 1 if self.card.sort_order == 'desc' else -1

            return 0

        self.listbox.set_sort_func(sort)

    def add_actions(self):
        # Menu actions in selection mode
        download_selected_chapters_action = Gio.SimpleAction.new('card.download-selected-chapters', None)
        download_selected_chapters_action.connect('activate', self.download_selected_chapters)
        self.card.window.application.add_action(download_selected_chapters_action)

        mark_selected_chapters_as_read_action = Gio.SimpleAction.new('card.mark-selected-chapters-as-read', None)
        mark_selected_chapters_as_read_action.connect('activate', self.toggle_selected_chapters_read_status, 1)
        self.card.window.application.add_action(mark_selected_chapters_as_read_action)

        mark_selected_chapters_as_unread_action = Gio.SimpleAction.new('card.mark-selected-chapters-as-unread', None)
        mark_selected_chapters_as_unread_action.connect('activate', self.toggle_selected_chapters_read_status, 0)
        self.card.window.application.add_action(mark_selected_chapters_as_unread_action)

        reset_selected_chapters_action = Gio.SimpleAction.new('card.reset-selected-chapters', None)
        reset_selected_chapters_action.connect('activate', self.reset_selected_chapters)
        self.card.window.application.add_action(reset_selected_chapters_action)

        select_all_chapters_action = Gio.SimpleAction.new('card.select-all-chapters', None)
        select_all_chapters_action.connect('activate', self.select_all)
        self.card.window.application.add_action(select_all_chapters_action)

        # Chapters menu actions
        download_chapter_action = Gio.SimpleAction.new('card.download-chapter', None)
        download_chapter_action.connect('activate', self.download_chapter)
        self.card.window.application.add_action(download_chapter_action)

        mark_chapter_as_read_action = Gio.SimpleAction.new('card.mark-chapter-as-read', None)
        mark_chapter_as_read_action.connect('activate', self.toggle_chapter_read_status, 1)
        self.card.window.application.add_action(mark_chapter_as_read_action)

        mark_chapter_as_unread_action = Gio.SimpleAction.new('card.mark-chapter-as-unread', None)
        mark_chapter_as_unread_action.connect('activate', self.toggle_chapter_read_status, 0)
        self.card.window.application.add_action(mark_chapter_as_unread_action)

        reset_chapter_action = Gio.SimpleAction.new('card.reset-chapter', None)
        reset_chapter_action.connect('activate', self.reset_chapter)
        self.card.window.application.add_action(reset_chapter_action)

    def clear(self):
        for row in self.listbox.get_children():
            row.destroy()

    def download_chapter(self, action, param):
        # Add chapter in download queue
        self.card.window.downloader.add(self.action_row.chapter)

        self.card.window.downloader.start()

    def download_selected_chapters(self, action, param):
        for row in self.listbox.get_selected_rows():
            # Add chapter in download queue
            self.card.window.downloader.add(row.chapter)

        self.card.window.downloader.start()

        self.card.leave_selection_mode()

    def enter_selection_mode(self):
        self.selection_mode_count = 0
        self.selection_mode_last_row_index = None

        self.listbox.set_selection_mode(Gtk.SelectionMode.MULTIPLE)

    def leave_selection_mode(self):
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        for row in self.listbox.get_children():
            row._selected = False

    def on_chapter_row_clicked(self, listbox, row):
        _ret, state = Gtk.get_current_event_state()
        modifiers = Gtk.accelerator_get_default_mod_mask()

        # Enter selection mode if <Control>+Click or <Shift>+Click is done
        if state & modifiers in (Gdk.ModifierType.CONTROL_MASK, Gdk.ModifierType.SHIFT_MASK) and not self.card.selection_mode:
            self.card.enter_selection_mode()

        if self.card.selection_mode:
            if state & modifiers == Gdk.ModifierType.SHIFT_MASK:
                # Enter range selection mode if <Shift>+Click is done
                self.selection_mode_range = True
            if self.selection_mode_range and self.selection_mode_last_row_index is not None:
                # Range selection mode: select all rows between last selected row and clicked row
                walk_index = self.selection_mode_last_row_index
                last_index = row.get_index()

                while walk_index != last_index:
                    walk_row = self.listbox.get_row_at_index(walk_index)
                    if walk_row and not walk_row._selected:
                        self.selection_mode_count += 1
                        self.listbox.select_row(walk_row)
                        walk_row._selected = True

                    if walk_index < last_index:
                        walk_index += 1
                    else:
                        walk_index -= 1

            self.selection_mode_range = False

            if row._selected:
                self.listbox.unselect_row(row)
                self.selection_mode_count -= 1
                self.selection_mode_last_row_index = None
                row._selected = False
            else:
                self.listbox.select_row(row)
                self.selection_mode_count += 1
                self.selection_mode_last_row_index = row.get_index()
                row._selected = True

            if self.selection_mode_count == 0:
                self.card.leave_selection_mode()
        else:
            self.card.window.reader.init(self.card.manga, row.chapter)

    def on_gesture_long_press_activated(self, gesture, x, y):
        if self.card.selection_mode:
            # Enter in 'Range' selection mode
            # Long press on a chapter row then long press on another to select everything in between
            self.selection_mode_range = True
        else:
            self.card.enter_selection_mode()

    def populate(self):
        def run():
            for row in self.listbox.get_children():
                GLib.idle_add(self.populate_chapter_row, row)
                time.sleep(0.0025)

        # Use countdown to stop activity indicator
        self.populate_countdown = len(self.card.manga.chapters)

        for chapter in self.card.manga.chapters:
            row = Gtk.ListBoxRow()
            row.get_style_context().add_class('card-chapter-listboxrow')
            row.chapter = chapter
            row.download = None
            row._selected = False
            self.listbox.add(row)

        if self.card.manga.chapters:
            thread = threading.Thread(target=run)
            thread.daemon = True
            thread.start()

    def populate_chapter_row(self, row):
        children = row.get_children()
        if children:
            update = True
            for child in children:
                child.destroy()
        else:
            update = False

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        row.add(box)

        chapter = row.chapter

        #
        # Title, scanlators, action button
        #
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        # Title
        label = Gtk.Label(xalign=0)
        label.set_valign(Gtk.Align.CENTER)
        ctx = label.get_style_context()
        ctx.add_class('card-chapter-label')
        if chapter.read:
            # Chapter reading ended
            ctx.add_class('dim-label')
        elif chapter.last_page_read_index is not None:
            # Chapter reading started
            ctx.add_class('card-chapter-label-started')
        label.set_line_wrap(True)
        title = chapter.title
        if self.card.manga.name != title and self.card.manga.name in title:
            title = title.replace(self.card.manga.name, '').strip()
        label.set_markup(html_escape(title))

        if chapter.scanlators:
            # Vertical box for title and scanlators
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

            # Add title
            vbox.pack_start(label, True, True, 0)

            # Scanlators
            label = Gtk.Label(xalign=0)
            label.set_valign(Gtk.Align.CENTER)
            ctx = label.get_style_context()
            ctx.add_class('dim-label')
            ctx.add_class('card-chapter-sublabel')
            label.set_line_wrap(True)
            label.set_markup(html_escape(', '.join(chapter.scanlators)))
            vbox.pack_start(label, True, True, 0)

            hbox.pack_start(vbox, True, True, 0)
        else:
            # Title only
            hbox.pack_start(label, True, True, 0)

        # Action button
        button = Gtk.Button.new_from_icon_name('view-more-symbolic', Gtk.IconSize.BUTTON)
        button.connect('clicked', self.show_chapter_menu, row)
        button.set_relief(Gtk.ReliefStyle.NONE)
        hbox.pack_start(button, False, True, 0)

        box.pack_start(hbox, True, True, 0)

        #
        # Recent badge, date, download status, page counter
        #
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        # Recent badge
        if chapter.recent == 1:
            label = Gtk.Label(xalign=0, yalign=1)
            label.set_valign(Gtk.Align.CENTER)
            ctx = label.get_style_context()
            ctx.add_class('card-chapter-sublabel')
            ctx.add_class('badge')
            label.set_text(_('New'))
            hbox.pack_start(label, False, True, 0)

        # Date + Download status (text or progress bar)
        download_status = None
        if chapter.downloaded:
            download_status = 'downloaded'
        else:
            if row.download is None:
                row.download = Download.get_by_chapter_id(chapter.id)
            if row.download:
                download_status = row.download.status

        label = Gtk.Label(xalign=0, yalign=1)
        label.set_valign(Gtk.Align.CENTER)
        label.get_style_context().add_class('card-chapter-sublabel')
        text = chapter.date.strftime(_('%m/%d/%Y')) if chapter.date else ''
        if download_status is not None and download_status != 'downloading':
            text = f'{text} - {_(Download.STATUSES[download_status]).upper()}'
        label.set_text(text)

        if download_status == 'downloading':
            hbox.pack_start(label, False, False, 0)

            # Download progress
            progressbar = Gtk.ProgressBar()
            progressbar.set_valign(Gtk.Align.CENTER)
            progressbar.set_fraction(row.download.percent / 100)
            hbox.pack_start(progressbar, True, True, 0)

            stop_button = Gtk.Button.new_from_icon_name('media-playback-stop-symbolic', Gtk.IconSize.MENU)
            stop_button.connect('clicked', lambda button, chapter: self.card.window.downloader.remove(chapter), chapter)
            hbox.pack_start(stop_button, False, False, 0)
        else:
            hbox.pack_start(label, True, True, 0)

            # Counter: nb read / nb pages
            if not chapter.read:
                label = Gtk.Label(xalign=0.5, yalign=1)
                label.set_valign(Gtk.Align.CENTER)
                label.get_style_context().add_class('card-chapter-sublabel')
                if chapter.pages is not None and chapter.last_page_read_index is not None:
                    label.set_text(f'{chapter.last_page_read_index + 1}/{len(chapter.pages)}')
                hbox.pack_start(label, False, True, 0)

        box.pack_start(hbox, True, True, 0)

        if update:
            box.show_all()
        else:
            row.show_all()

        if self.populate_countdown:
            self.populate_countdown -= 1
            if self.populate_countdown == 0:
                # All rows have been populated/updated
                self.card.window.activity_indicator.stop()

                if self.card.selection_mode:
                    self.card.leave_selection_mode()

    def refresh(self, chapters):
        for chapter in chapters:
            self.update_chapter_row(chapter=chapter)

    def reset_chapter(self, action, param):
        chapter = self.action_row.chapter

        chapter.reset()

        self.populate_chapter_row(self.action_row)

    def reset_selected_chapters(self, action, param):
        for row in self.listbox.get_selected_rows():
            chapter = row.chapter

            chapter.reset()

            self.populate_chapter_row(row)

        self.card.leave_selection_mode()

    def select_all(self, action=None, param=None):
        if not self.card.selection_mode:
            self.card.enter_selection_mode()

        self.selection_mode_count = len(self.listbox.get_children())

        for row in self.listbox.get_children():
            if row._selected:
                continue
            self.listbox.select_row(row)
            row._selected = True

    def show_chapter_menu(self, button, row):
        chapter = row.chapter
        self.action_row = row

        popover = Gtk.Popover(border_width=4)
        popover.set_position(Gtk.PositionType.BOTTOM)
        popover.set_relative_to(button)

        menu = Gio.Menu()
        if chapter.downloaded or chapter.last_page_read_index is not None:
            menu.append(_('Reset'), 'app.card.reset-chapter')
        if not chapter.downloaded:
            menu.append(_('Download'), 'app.card.download-chapter')
        if not chapter.read:
            menu.append(_('Mark as read'), 'app.card.mark-chapter-as-read')
        if chapter.read or chapter.last_page_read_index is not None:
            menu.append(_('Mark as unread'), 'app.card.mark-chapter-as-unread')

        popover.bind_model(menu, None)
        popover.popup()

    def toggle_selected_chapters_read_status(self, action, param, read):
        chapters_ids = []
        chapters_data = []

        self.card.window.activity_indicator.start()

        # First, update DB
        for row in self.listbox.get_selected_rows():
            chapter = row.chapter

            if chapter.pages:
                pages = deepcopy(chapter.pages)
                for page in pages:
                    page['read'] = read
            else:
                pages = None
            last_page_read_index = None if chapter.read == read == 0 else chapter.last_page_read_index

            chapters_ids.append(chapter.id)
            chapters_data.append(dict(
                pages=pages,
                read=read,
                recent=False,
                last_page_read_index=last_page_read_index,
            ))

        db_conn = create_db_connection()

        with db_conn:
            res = update_rows(db_conn, 'chapters', chapters_ids, chapters_data)

        db_conn.close()

        # Then, if DB update succeeded, refresh chapters rows
        def run():
            # Use countdown to stop activity indicator and leave selection mode
            self.populate_countdown = len(self.listbox.get_selected_rows())

            for row in self.listbox.get_selected_rows():
                chapter = row.chapter

                if chapter.pages:
                    for chapter_page in chapter.pages:
                        chapter_page['read'] = read
                if chapter.read == read == 0:
                    chapter.last_page_read_index = None
                chapter.read = read
                chapter.recent = False

                GLib.idle_add(self.populate_chapter_row, row)
                time.sleep(0.0025)

        if res:
            thread = threading.Thread(target=run)
            thread.daemon = True
            thread.start()
        else:
            self.card.window.activity_indicator.stop()
            self.card.leave_selection_mode()

    def toggle_chapter_read_status(self, action, param, read):
        chapter = self.action_row.chapter

        if chapter.pages:
            for chapter_page in chapter.pages:
                chapter_page['read'] = read

        data = dict(
            pages=chapter.pages,
            read=read,
            recent=False,
        )
        if chapter.read == read == 0:
            data['last_page_read_index'] = None

        if chapter.update(data):
            self.populate_chapter_row(self.action_row)

    def update_chapter_row(self, downloader=None, download=None, chapter=None):
        """
        Update a specific chapter row
        - used when download status change (via signal from Downloader)
        - used when we come back from reader to update last page read
        """
        if chapter is None:
            chapter = download.chapter

        if self.card.window.page not in ('card', 'reader') or self.card.manga.id != chapter.manga_id:
            return

        for row in self.listbox.get_children():
            if row.chapter.id == chapter.id:
                row.chapter = chapter
                row.download = download
                self.populate_chapter_row(row)
                break


class InfoGrid:
    def __init__(self, card):
        self.card = card

        grid = self.card.builder.get_object('card_page_grid')
        grid.set_margin_start(6)
        grid.set_margin_end(6)

        self.cover_image = self.card.builder.get_object('cover_image')
        self.authors_value_label = self.card.builder.get_object('authors_value_label')
        self.genres_value_label = self.card.builder.get_object('genres_value_label')
        self.status_value_label = self.card.builder.get_object('status_value_label')
        self.scanlators_value_label = self.card.builder.get_object('scanlators_value_label')
        self.server_value_label = self.card.builder.get_object('server_value_label')
        self.last_update_value_label = self.card.builder.get_object('last_update_value_label')
        self.synopsis_value_label = self.card.builder.get_object('synopsis_value_label')
        self.more_label = self.card.builder.get_object('more_label')

    def populate(self):
        manga = self.card.manga

        if manga.cover_fs_path is None:
            pixbuf = Pixbuf.new_from_resource_at_scale('/info/febvre/Komikku/images/missing_file.png', 174, -1, True)
        else:
            if get_file_mime_type(manga.cover_fs_path) != 'image/gif':
                pixbuf = Pixbuf.new_from_file_at_scale(manga.cover_fs_path, 174, -1, True)
            else:
                pixbuf = scale_pixbuf_animation(PixbufAnimation.new_from_file(manga.cover_fs_path), 174, -1, True, True)

        if isinstance(pixbuf, PixbufAnimation):
            self.cover_image.set_from_animation(pixbuf)
        else:
            self.cover_image.set_from_pixbuf(pixbuf)

        authors = html_escape(', '.join(manga.authors)) if manga.authors else '-'
        self.authors_value_label.set_markup('<span size="small">{0}</span>'.format(authors))

        genres = html_escape(', '.join(manga.genres)) if manga.genres else '-'
        self.genres_value_label.set_markup('<span size="small">{0}</span>'.format(genres))

        status = _(manga.STATUSES[manga.status]) if manga.status else '-'
        self.status_value_label.set_markup('<span size="small">{0}</span>'.format(status))

        scanlators = html_escape(', '.join(manga.scanlators)) if manga.scanlators else '-'
        self.scanlators_value_label.set_markup('<span size="small">{0}</span>'.format(scanlators))

        self.server_value_label.set_markup(
            '<span size="small">{0} [{1}] - {2} chapters</span>'.format(
                html_escape(manga.server.name), manga.server.lang.upper(), len(manga.chapters)
            )
        )

        self.last_update_value_label.set_markup(
            '<span size="small">{0}</span>'.format(manga.last_update.strftime('%m/%d/%Y')) if manga.last_update else '-')

        self.synopsis_value_label.set_text(manga.synopsis or '-')

        self.set_disk_usage()

    def refresh(self):
        self.set_disk_usage()

    def set_disk_usage(self):
        self.more_label.set_markup('<i>{0}</i>'.format(_('Disk space used: {0}').format(folder_size(self.card.manga.path))))
