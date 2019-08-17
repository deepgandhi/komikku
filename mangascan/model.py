# -*- coding: utf-8 -*-

# Copyright (C) 2019 Valéry Febvre
# SPDX-License-Identifier: GPL-3.0-only or GPL-3.0-or-later
# Author: Valéry Febvre <vfebvre@easter-eggs.com>

import datetime
from gettext import gettext as _
import importlib
import json
import os
from pathlib import Path
import sqlite3
import shutil


user_app_dir_path = os.path.join(str(Path.home()), 'MangaScan')
db_path = os.path.join(user_app_dir_path, 'mangascan.db')


def adapt_json(data):
    return (json.dumps(data, sort_keys=True)).encode()


def convert_json(blob):
    return json.loads(blob.decode())


sqlite3.register_adapter(dict, adapt_json)
sqlite3.register_adapter(list, adapt_json)
sqlite3.register_adapter(tuple, adapt_json)
sqlite3.register_converter('json', convert_json)


def create_db_connection():
    con = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
    if con is None:
        print("Error: Can not create the database connection.")
        return None

    con.row_factory = sqlite3.Row
    return con


def create_table(conn, sql):
    try:
        c = conn.cursor()
        c.execute(sql)
    except Exception as e:
        print('SQLite-error:', e)


def init_db():
    if not os.path.exists(user_app_dir_path):
        os.mkdir(user_app_dir_path)

    sql_create_mangas_table = """CREATE TABLE IF NOT EXISTS mangas (
        id integer PRIMARY KEY,
        slug text NOT NULL,
        server_id text NOT NULL,
        name text NOT NULL,
        author text,
        genres json,
        synopsis text,
        status text,
        sort_order text,
        filters json,
        reading_direction text,
        background_color text,
        scaling text,
        last_read timestamp,
        last_update timestamp,
        UNIQUE (slug, server_id)
    );"""

    sql_create_chapters_table = """CREATE TABLE IF NOT EXISTS chapters (
        id integer PRIMARY KEY,
        slug text NOT NULL,
        manga_id integer REFERENCES mangas(id) ON DELETE CASCADE,
        title text NOT NULL,
        pages json,
        date text,
        rank integer NOT NULL,
        downloaded integer NOT NULL,
        recent integer NOT NULL,
        read integer NOT NULL,
        last_page_read_index integer,
        UNIQUE (slug, manga_id)
    );"""

    sql_create_downloads_table = """CREATE TABLE IF NOT EXISTS downloads (
        id integer PRIMARY KEY,
        chapter_id integer REFERENCES chapters(id) ON DELETE CASCADE,
        status text,
        percent float,
        date timestamp,
        UNIQUE (chapter_id)
    );"""

    db_conn = create_db_connection()
    if db_conn is not None:
        create_table(db_conn, sql_create_mangas_table)
        create_table(db_conn, sql_create_chapters_table)
        create_table(db_conn, sql_create_downloads_table)

        db_conn.close()


def insert_row(db_conn, table, data):
    try:
        cursor = db_conn.execute(
            'INSERT INTO {0} ({1}) VALUES ({2})'.format(table, ', '.join(data.keys()), ', '.join(['?'] * len(data))),
            tuple(data.values())
        )
        return cursor.lastrowid
    except Exception as e:
        print('SQLite-error:', e, table, data)
        return None


def update_row(db_conn, table, id, data):
    db_conn.execute(
        'UPDATE {0} SET {1} WHERE id = ?'.format(table, ', '.join([k + ' = ?' for k in data.keys()])),
        tuple(data.values()) + (id,)
    )


class Manga(object):
    chapters_ = None
    server = None

    STATUSES = dict(
        complete=_('Complete'),
        ongoing=_('Ongoing')
    )

    def __init__(self, id=None, server=None):
        if server:
            self.server = server

        if id is not None:
            db_conn = create_db_connection()
            row = db_conn.execute('SELECT * FROM mangas WHERE id = ?', (id,)).fetchone()
            for key in row.keys():
                setattr(self, key, row[key])

            if server is None:
                server_module = importlib.import_module('.' + self.server_id, package="mangascan.servers")
                self.server = getattr(server_module, self.server_id.capitalize())()

    @classmethod
    def new(cls, data, server=None):
        m = cls(server=server)

        data = data.copy()
        chapters = data.pop('chapters')
        cover_path_or_url = data.pop('cover')

        # Fill data with internal data or later scraped values
        data.update(dict(
            last_read=datetime.datetime.now(),
            sort_order=None,
            reading_direction=None,
            last_update=None,
        ))

        for key in data.keys():
            setattr(m, key, data[key])

        db_conn = create_db_connection()
        with db_conn:
            m.id = insert_row(db_conn, 'mangas', data)
        db_conn.close()

        m.chapters_ = []
        for rank, chapter_data in enumerate(chapters):
            chapter = Chapter.new(chapter_data, rank, m.id)
            if chapter is not None:
                m.chapters_ = [chapter, ] + m.chapters_

        if not os.path.exists(m.path):
            os.makedirs(m.path)

        m._save_cover(cover_path_or_url)

        return m

    @property
    def chapters(self):
        if self.chapters_ is None:
            db_conn = create_db_connection()
            if self.sort_order == 'asc':
                rows = db_conn.execute('SELECT * FROM chapters WHERE manga_id = ? ORDER BY rank ASC', (self.id,))
            else:
                rows = db_conn.execute('SELECT * FROM chapters WHERE manga_id = ? ORDER BY rank DESC', (self.id,))

            self.chapters_ = []
            for row in rows:
                self.chapters_.append(Chapter(row=row))

            db_conn.close()

        return self.chapters_

    @property
    def cover_fs_path(self):
        path = os.path.join(self.path, 'cover.jpg')
        if os.path.exists(path):
            return path
        else:
            return None

    @property
    def nb_recent_chapters(self):
        db_conn = create_db_connection()
        row = db_conn.execute('SELECT count() AS recents FROM chapters WHERE manga_id = ? AND recent = 1', (self.id,)).fetchone()
        db_conn.close()

        return row['recents']

    @property
    def path(self):
        return os.path.join(str(Path.home()), 'MangaScan', self.server_id, self.name)

    def delete(self):
        db_conn = create_db_connection()
        # Enable integrity constraint
        db_conn.execute('PRAGMA foreign_keys = ON')

        with db_conn:
            db_conn.execute('DELETE FROM mangas WHERE id = ?', (self.id, ))

            if os.path.exists(self.path):
                shutil.rmtree(self.path)

        db_conn.close()

    def _save_cover(self, path_or_url):
        if path_or_url is None:
            return

        # Save cover image file
        cover_data = self.server.get_manga_cover_image(path_or_url)
        if cover_data is not None:
            cover_fs_path = os.path.join(self.path, 'cover.jpg')

            with open(cover_fs_path, 'wb') as fp:
                fp.write(cover_data)

    def update(self, data):
        """
        Updates specific fields

        :param dict data: fields to update
        :return: True on success False otherwise
        """
        # Update
        for key in data.keys():
            setattr(self, key, data[key])

        db_conn = create_db_connection()
        with db_conn:
            update_row(db_conn, 'mangas', self.id, data)

        db_conn.close()

        return True

    def update_full(self):
        """
        Updates manga

        Fetches and saves data available in manga's HTML page on server

        :return: True on success False otherwise, number of recent chapters
        :rtype: tuple
        """
        db_conn = create_db_connection()
        with db_conn:
            data = self.server.get_manga_data(dict(slug=self.slug, name=self.name))
            if data is None:
                return False, 0

            # Update cover
            if data.get('cover'):
                self._save_cover(data.pop('cover'))

            # Update chapters
            chapters_data = data.pop('chapters')
            nb_recent_chapters = 0

            for rank, chapter_data in enumerate(chapters_data):
                row = db_conn.execute(
                    'SELECT * FROM chapters WHERE manga_id = ? AND slug = ?', (self.id, chapter_data['slug'])
                ).fetchone()
                if row:
                    # Update chapter
                    update_row(db_conn, 'chapters', row['id'], chapter_data)
                else:
                    # Add new chapter
                    chapter_data.update(dict(
                        manga_id=self.id,
                        rank=rank,
                        downloaded=0,
                        recent=1,
                        read=0,
                    ))
                    insert_row(db_conn, 'chapters', chapter_data)
                    nb_recent_chapters += 1

            if nb_recent_chapters > 0:
                data['last_update'] = datetime.datetime.now()

            # Delete chapters that no longer exist
            chapters_slugs = [chapter_data['slug'] for chapter_data in chapters_data]
            rows = db_conn.execute('SELECT * FROM chapters WHERE manga_id = ?', (self.id,))
            for row in rows:
                if row['slug'] not in chapters_slugs:
                    db_conn.execute('DELETE FROM chapters WHERE id = ?', (row['id'],))

            self.chapters_ = None

            # Update
            for key in data.keys():
                setattr(self, key, data[key])

            update_row(db_conn, 'mangas', self.id, data)

        db_conn.close()

        return True, nb_recent_chapters


class Chapter(object):
    manga_ = None

    def __init__(self, id=None, row=None):
        if id or row:
            if id:
                db_conn = create_db_connection()
                row = db_conn.execute('SELECT * FROM chapters WHERE id = ?', (id,)).fetchone()
                db_conn.close()

            for key in row.keys():
                setattr(self, key, row[key])

    @property
    def manga(self):
        if self.manga_ is None:
            self.manga_ = Manga(self.manga_id)

        return self.manga_

    @property
    def path(self):
        return os.path.join(self.manga.path, self.slug)

    @classmethod
    def new(cls, data, rank, manga_id):
        c = cls()

        # Fill data with internal usage data or not yet scraped values
        data = data.copy()
        data.update(dict(
            manga_id=manga_id,
            pages=None,  # later scraped value
            rank=rank,
            downloaded=0,
            recent=0,
            read=0,
            last_page_read_index=None,
        ))

        for key in data.keys():
            setattr(c, key, data[key])

        db_conn = create_db_connection()
        with db_conn:
            c.id = insert_row(db_conn, 'chapters', data)

        db_conn.close()

        return c if c.id is not None else None

    def get_page(self, page_index):
        page_path = self.get_page_path(page_index)
        if page_path:
            return page_path

        imagename, data = self.manga.server.get_manga_chapter_page_image(self.manga.slug, self.slug, self.pages[page_index])

        if imagename and data:
            if not os.path.exists(self.path):
                os.mkdir(self.path)

            page_path = os.path.join(self.path, imagename)
            with open(page_path, 'wb') as fp:
                fp.write(data)

            if self.pages[page_index]['image'] is None:
                self.pages[page_index]['image'] = imagename
                self.update(dict(pages=self.pages))

            return page_path
        else:
            return None

    def get_page_path(self, page_index):
        # self.pages[page_index]['image'] can be an image name or an image path
        imagename = self.pages[page_index]['image'].split('/')[-1] if self.pages[page_index]['image'] else None

        if imagename is not None:
            path = os.path.join(self.path, imagename)

            return path if os.path.exists(path) else None
        else:
            return None

    def reset(self):
        if os.path.exists(self.path):
            shutil.rmtree(self.path)

        self.update(dict(
            pages=None,
            downloaded=0,
            read=0,
            last_page_read_index=None,
        ))

    def update(self, data):
        """
        Updates specific fields

        :param dict data: fields to update
        :return: True on success False otherwise
        """
        for key in data.keys():
            setattr(self, key, data[key])

        db_conn = create_db_connection()
        with db_conn:
            update_row(db_conn, 'chapters', self.id, data)

        db_conn.close()

        return True

    def update_full(self):
        """
        Updates chapter

        Fetches and saves data available in chapter's HTML page on server

        :return: True on success False otherwise
        """
        if self.pages:
            return True

        data = self.manga.server.get_manga_chapter_data(self.manga.slug, self.slug)
        if data is None:
            return False

        return self.update(data)


class Download(object):
    STATUSES = dict(
        pending=_('Pending'),
        downloading=_('Downloading'),
        error=_('Error'),
    )

    def __init__(self):
        pass

    @classmethod
    def get_by_chapter_id(cls, chapter_id):
        db_conn = create_db_connection()
        row = db_conn.execute('SELECT * FROM downloads WHERE chapter_id = ?', (chapter_id,)).fetchone()
        db_conn.close()

        if row:
            c = cls()

            for key in row.keys():
                setattr(c, key, row[key])

            return c
        else:
            return None

    @classmethod
    def new(cls, chapter_id):
        db_conn = create_db_connection()
        row = db_conn.execute('SELECT * FROM downloads WHERE chapter_id = ?', (chapter_id,)).fetchone()
        db_conn.close()
        if row:
            return None

        c = cls()
        data = dict(
            chapter_id=chapter_id,
            status='pending',
            percent=0,
            date=datetime.datetime.now(),
        )

        for key in data.keys():
            setattr(c, key, data[key])

        db_conn = create_db_connection()
        with db_conn:
            c.id = insert_row(db_conn, 'downloads', data)

        db_conn.close()

        return c

    def delete(self):
        db_conn = create_db_connection()
        # Enable integrity constraint
        db_conn.execute('PRAGMA foreign_keys = ON')

        with db_conn:
            db_conn.execute('DELETE FROM downloads WHERE id = ?', (self.id, ))

        db_conn.close()

    @classmethod
    def next(cls):
        db_conn = create_db_connection()
        row = db_conn.execute('SELECT * FROM downloads ORDER BY date DESC').fetchone()
        db_conn.close()

        if row:
            c = cls()

            for key in row.keys():
                setattr(c, key, row[key])

            return c
        else:
            return None

    def update(self, data):
        """
        Updates download

        :param data: percent of pages downloaded and/or status
        :return: True on success False otherwise
        """

        db_conn = create_db_connection()
        with db_conn:
            update_row(db_conn, 'downloads', self.id, data)

        db_conn.close()

        return True
