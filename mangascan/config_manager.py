from gi.repository import GLib, Gio

setting = Gio.Settings.new('com.gitlab.valos.MangaScan')

dark_theme = 'dark-theme'
reading_direction = 'reading-direction'
scaling = 'scaling'
window_size = 'window-size'
development_backup_mode = 'development-backup-mode'


def get_dark_theme():
    return setting.get_boolean(dark_theme)


def set_dark_theme(value):
    setting.set_boolean(dark_theme, value)


def get_reading_direction(nick=True):
    value = setting.get_enum(reading_direction)
    if not nick:
        return value

    if value == 0:
        return 'right-to-left'
    elif value == 1:
        return 'left-to-right'


def set_reading_direction(value):
    if value == 'right-to-left':
        setting.set_enum(reading_direction, 0)
    elif value == 'left-to-right':
        setting.set_enum(reading_direction, 1)


def get_scaling(nick=True):
    value = setting.get_enum(scaling)
    if not nick:
        return value

    if value == 0:
        return 'screen'
    elif value == 1:
        return 'width'
    elif value == 2:
        return 'height'


def set_scaling(value):
    if value == 'screen':
        setting.set_enum(scaling, 0)
    elif value == 'width':
        setting.set_enum(scaling, 1)
    elif value == 'height':
        setting.set_enum(scaling, 2)


def get_window_size():
    return setting.get_value(window_size)


def set_window_size(list):
    g_variant = GLib.Variant('ai', list)
    setting.set_value(window_size, g_variant)


def get_development_backup_mode():
    return setting.get_boolean(development_backup_mode)


def set_development_backup_mode(value):
    setting.set_boolean(development_backup_mode, value)
