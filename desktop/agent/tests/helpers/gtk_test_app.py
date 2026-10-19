"""A tiny GTK3 window used only by the Linux backend test. It prints what happens to it."""

import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

win = Gtk.Window(title="KMR Test Window")
win.set_default_size(360, 260)
box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
win.add(box)
box.add(Gtk.Label(label="Welcome to the test window"))

box.add(Gtk.Label(label="Error: could not load the page"))
box.add(Gtk.Label(label="Contact jane.doe@example.com or call 9876543210"))

reload_button = Gtk.Button(label="Reload")
reload_button.connect("clicked", lambda *_: print("EVENT pressed:reload", flush=True))
box.add(reload_button)

settings = Gtk.Button(label="Open settings")
settings.connect("clicked", lambda *_: print("EVENT pressed:open-settings", flush=True))
box.add(settings)

unnamed = Gtk.Button()  # no label and no name: a control with no accessible name
unnamed.connect("clicked", lambda *_: print("EVENT pressed:unnamed", flush=True))
box.add(unnamed)

submit = Gtk.Button(label="Submit order")
submit.connect("clicked", lambda *_: print("EVENT pressed:submit", flush=True))
box.add(submit)

search = Gtk.Entry()
search.get_accessible().set_name("Search")
search.connect("changed", lambda e: print("EVENT text:search:" + e.get_text(), flush=True))
box.add(search)

password = Gtk.Entry()
password.set_visibility(False)
password.get_accessible().set_name("Password")
password.connect("changed", lambda e: print("EVENT text:password", flush=True))
box.add(password)

win.connect("destroy", Gtk.main_quit)
win.show_all()
GLib.timeout_add_seconds(int(sys.argv[1]) if len(sys.argv) > 1 else 40, Gtk.main_quit)
print("READY", flush=True)
Gtk.main()
