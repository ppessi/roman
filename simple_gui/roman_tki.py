#!/usr/bin/env python3
# -*- encoding: utf-8 -*-

import appdirs
import sys
import tkinter as tk
from collections import OrderedDict
from configparser import ConfigParser
from os import environ, makedirs
from os.path import expanduser, exists, isdir, isfile, join, split as split_path
from queue import Queue, Empty
from threading import Thread
from tkinter import filedialog, messagebox
from tkinter import ttk

from apluslms_roman import __app_id__, CourseConfig, Engine
from apluslms_roman.backends.docker import DockerBackend
from apluslms_roman.observer import (
    Phase,
    Message,
    BuildObserver,
)


UPDATE_INTERVAL = 40
FILE_TYPES = (
    ('Course config', '*.yml *.yaml *.json'),
    ('All files', '*'),
)


def path_end(path, num=1):
    parts = []
    while path and len(parts) < num:
        path, tail = split_path(path)
        if tail: parts.append(tail)
    return join(*reversed(parts))


def resource_path(filename):
    # we are inside singlefile pyinstaller
    if getattr(sys, 'frozen', False):
        if hasattr(sys, '_MEIPASS'):
            # pyinstaller
            return join(sys._MEIPASS, filename)
        elif 'RESOURCEPATH' in environ:
            # py2app
            return join(environ['RESOURCEPATH'], filename)
    dir_, _ = split_path(__file__)
    return join(dir_, filename)


def settings_id_to_sec_and_key(id_, def_sec):
    if isinstance(id_, tuple):
        if len(tuple) != 2:
            raise ValueError("Invalid identifier: expected two field tuple or string")
        section, key = id_
    else:
        parts = id_.split('/', 1)
        if len(parts) == 1:
            section = def_sec
            key = parts[0]
        else:
            section, key = parts
    return section, key

class Settings(OrderedDict):
    def __init__(self, app_id, settings=None):
        self._app_id = app_id
        author = app_id.rpartition('.')[0]
        self._dir = appdirs.user_config_dir(appname=app_id, appauthor=author)
        self._settings = self.get_path(settings or 'settings.ini')
        self._default_section = 'common'
        self._data = OrderedDict()
        super().__init__()

    def get_path(self, filename):
        return join(self._dir, filename)

    def set_defaults(self, section, options):
        data = self._data
        if section not in data:
            data[section] = OrderedDict()
        section = data[section]

        if isinstance(options, dict):
            options = options.items()

        for key, value in options:
            if key not in section:
                section[key] = (value, False)

    def load(self):
        parser = ConfigParser(interpolation=None)
        try:
            parser.read(self._settings)
        except IOError:
            return

        data = self._data
        for section in parser.sections():
            opts = parser[section]
            if section not in data: data[section] = OrderedDict()
            group = data[section]
            for key in opts.keys():
                type_ = type(group[key][0]) if key in group else str
                try:
                    value = opts.getboolean(key) if type_ is bool else type_(opts[key])
                except ValueError:
                    continue
                group[key] = (value, True)

    def safe(self):
        parser = ConfigParser()
        for section, values in self._data.items():
            vals = OrderedDict(((key, val) for key, (val, store) in values.items() if store))
            parser[section] = vals
        if not exists(self._dir):
            makedirs(self._dir, 0o700)
        try:
            with open(self._settings, 'w') as f:
                f.write("; Configuration {}\n".format(self._app_id))
                parser.write(f)
        except IOError as e:
            return e

    def __getitem__(self, id_):
        section, key = settings_id_to_sec_and_key(id_, self._default_section)
        return self._data[section][key][0]

    def __setitem__(self, id_, value):
        section, key = settings_id_to_sec_and_key(id_, self._default_section)
        data = self._data
        if section not in data: data[section] = OrderedDict()
        data[section][key] = (value, True)


class QueueObserver(BuildObserver):
    def __init__(self):
        self.q = Queue(maxsize=1024)
    def _message(self, phase, type_, step=None, data=None):
        self.q.put((phase, type_, step, data))

    def retrieve(self):
        try:
            while True:
                yield self.q.get_nowait()
        except Empty:
            return


class BuildTask(Thread):
    def __init__(self, engine, config):
        super().__init__()
        self._observer = obs = QueueObserver()
        self._builder = engine.create_builder(config, observer=obs)
        self.daemon = True

    def run(self):
        try:
            self._result = self._builder.build()
        except Exception as e:
            self._result = e

    def join(self):
        super().join()
        if isinstance(self._result, Exception):
            raise self._result
        return self._result

    def poll(self):
        alive = self.is_alive()
        msgs = list(self._observer.retrieve())
        return (alive, msgs)


class Console(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent)

        self.text = text = tk.Text(self, height=24, width=80, state=tk.DISABLED)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        text.bind("<1>", lambda event: text.focus_set())

        self.scroll = scroll = tk.Scrollbar(self)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        scroll.config(command=text.yview)
        text.config(yscrollcommand=scroll.set)

        text.configure(background='black', foreground="white", selectbackground="#505050")
        text.tag_configure('phase', foreground="#2ba90e")
        text.tag_configure('step', foreground="#a0ea11")
        text.tag_configure('manager', foreground="#11ddea")
        text.tag_configure('error', foreground="#ef3131")

        text.bind('<Button-3>', self.context_menu, add='')

    def context_menu(self, event):
        def copy():
            event.widget.event_generate('<Control-c>')

        event.widget.focus()
        menu = tk.Menu(None, tearoff=0, takefocus=0)
        menu.add_command(label="Copy", command=copy)
        menu.tk_popup(event.x_root+40, event.y_root+10, entry="0")
        return 'break'

    def write(self, line, tag=None):
        self.text.config(state=tk.NORMAL)
        self.text.insert(tk.END, str(line).rstrip() + '\n', tag)
        self.text.see(tk.END)
        self.text.config(state=tk.DISABLED)

    def clear(self):
        self.text.config(state=tk.NORMAL)
        self.text.delete(1.0, tk.END)
        self.text.config(state=tk.DISABLED)

    def scroll_top(self):
        self.text.config(state=tk.NORMAL)
        self.text.see(1.0)
        self.text.config(state=tk.DISABLED)


class Progress(ttk.Progressbar):
    def __init__(self, parent):
        super().__init__(parent, orient="horizontal", mode="determinate")
        self.reset()

    def reset(self):
        self.config(value=0)
        self._val = 0
        self._steps = 0

    def set_steps(self, steps):
        self.config(value=0, maximum=steps)
        self._val = -1
        self._steps = steps

    def move(self):
        if self._val < self._steps:
            self._val += 1
            self.config(value=self._val)


class Roman:
    def __init__(self, master):
        self.master = master
        master.protocol("WM_DELETE_WINDOW", self.quit)
        master.title("Roman - A+ LMS course builder")
        img_file = resource_path('roman.png')
        if isfile(img_file):
            master.call('wm', 'iconphoto', master._w, tk.Image("photo", file=img_file))

        self.settings = Settings(__app_id__, 'roman_tki.ini')
        self.settings.set_defaults('window', (('geometry', ''),))
        self.settings.set_defaults('course', (('lasttime', ''),))

        cols = 5

        self.initial_dir = expanduser("~")
        self.config_dir = None
        self.config = None
        self.engine = None
        self.build_task = None

        self.label = tk.Label(master, text="No course selected!")
        self.label.grid(columnspan=cols, sticky=tk.W)

        # Buttons
        self.course_btn = tk.Button(master, text="Open course", command=self.open)
        self.course_btn.grid(row=1)
        self.load_btn = tk.Button(master, text="Load conf", command=self.load, state=tk.DISABLED)
        self.load_btn.grid(row=1, column=1)
        self.build_btn = tk.Button(master, text="Build course", command=self.build, state=tk.DISABLED)
        self.build_btn.grid(row=1, column=2)
        self.validate_btn = tk.Button(master, text="Validate yaml", command=self.build, state=tk.DISABLED)
        self.validate_btn.grid(row=1, column=3)
        self.close_btn = tk.Button(master, text="Quit", command=self.quit)
        self.close_btn.grid(row=1, column=4)

        # Console output
        self.console = Console(master)
        self.console.grid(columnspan=cols, row=2)

        # Progress
        self.progress = Progress(master)
        self.progress.grid(columnspan=cols, row=3, sticky=tk.W+tk.E)

        # Status line
        self.status = tk.Label(master, text="", bd=1, relief=tk.SUNKEN, anchor=tk.W)
        self.status.grid(columnspan=cols, row=4, sticky=tk.W+tk.E+tk.S)

        self.set_status("No course selected")
        self.restore()
        self.load_engine()

    def restore(self):
        st = self.settings
        st.load()

        # position
        geo = st['window/geometry']
        if 'x' in geo and '+' in geo:
            saved_size, saved_pos = geo.split('+', 1)
            cur_size, cur_pos = self.master.geometry().split('+', 1)
            self.master.geometry("%s+%s" % (cur_size, saved_pos))

        # active course
        try:
            config = CourseConfig.find_from(os.getcwd())
        except Exception:
            pass
        else:
            self.config_dir = config.__path__
            self.load()

        if not self.config_dir:
            last_course = st['course/lasttime']
            if last_course and isdir(last_course):
                last_course_end = join('…', path_end(last_course, 2))
                r = messagebox.askquestion(
                    "Restore previous course",
                    "Last time you had course open at {}.\nShould we restore that?".format(last_course_end),
                    parent=self.master,
                )
                if r == 'yes':
                    self.config_dir = last_course
                    self.load()
                else:
                    self.initial_dir = last_course

    def store(self):
        st = self.settings
        st['window/geometry'] = self.master.geometry()
        if self.config_dir:
            st['course/lasttime'] = self.config_dir
        st.safe()

    def set_status(self, msg):
        self.status.config(text=str(msg).strip())
        self.status.update_idletasks()

    def lock(self):
        self.course_btn.config(state=tk.DISABLED)
        self.load_btn.config(state=tk.DISABLED)
        self.build_btn.config(state=tk.DISABLED)
        self.validate_btn.config(state=tk.DISABLED)
        self.close_btn.config(state=tk.DISABLED)

    def unlock(self):
        self.course_btn.config(state=tk.NORMAL)
        if self.config_dir:
            self.load_btn.config(state=tk.NORMAL)
        if self.config:
            self.build_btn.config(state=tk.NORMAL)
        #self.validate_btn.config(state=tk.NORMAL)
        self.close_btn.config(state=tk.NORMAL)

    def load_engine(self):
        self.engine = engine = Engine()
        error = engine.verify()
        if error:
            messagebox.showerror(
                "Backend failed",
                "{} failed: {}".format(engine.backend.__class__.__name__, error),
                parent=self.master
            )

            self.console.write(
                "---\n{} failed to connect. Make sure that you have correct settings.\n  >> {}"
                    .format(engine.backend.__class__.__name__, error),
                'error',
            )
            if isinstance(engine.backend, DockerBackend):
                self.console.write("""
Do you have docker-ce installed and running?
Are you in local 'docker' group? Have you logged out and back in after joining?
You might be able to add yourself to that group with 'sudo adduser docker'.
""", 'error')

            self.engine = None
        else:
            self.console.write('---')
            self.console.write(engine.version_info(), 'manager')
            self.console.scroll_top()

    def open(self):
        self.set_status('')
        dir_ = filedialog.askdirectory(
            title="Select course directory",
            initialdir=self.config_dir or self.initial_dir,
            parent=self.master,
        )
        if not dir_: return
        if not isdir(dir_):
            self.set_status("Error: invalid selection")
            self.label.config(text="No course selected!")
            return

        self.load_btn.config(state=tk.DISABLED)
        self.build_btn.config(state=tk.DISABLED)
        self.config_dir = dir_
        self.load_btn.config(state=tk.NORMAL)
        self.load()

    def load(self):
        if not self.config_dir:
            self.set_status("Error: no course dir selected")


        self.build_btn.config(state=tk.DISABLED)
        self.console.clear()
        self.progress.reset()
        try:
            self.config = CourseConfig.find_from(self.config_dir)
        except Exception as e:
            self.set_status("Error: {}".format(e))
            raise

        self.label.config(text="Course: {}".format(self.config.name))
        self.set_status('course loaded')
        self.load_btn.config(state=tk.NORMAL)
        self.build_btn.config(state=tk.NORMAL)

        path = self.config.__source__
        if isfile(path):
            self.console.write("Configuration in {}".format(path), 'manager')
            with open(path) as f:
                for line in f:
                    self.console.write(line)
        else:
            self.console.write("Using legacy course configuration", 'error')
            self.console.write("""
This is totally fine and works well with a-plus-rst-tools and sphinx.
Though, it's recommended you add course.yml to your course directory.

Used configuration:""", 'step')
            self.console.write(self.config)

    def build(self):
        if not self.config:
            self.set_status("Error: build initiated without config")
            return

        if not self.engine:
            self.load_engine()
            return

        steps = len(self.config.steps)
        self.progress.set_steps(steps * 2)

        self.lock()
        self.build_task = build_task = BuildTask(self.engine, self.config)
        build_task.start()
        self.set_status("building...")
        self.console.clear()

        status_texts = {
            Phase.PREPARE: "Preparing",
            Phase.BUILD: "Building",
            Phase.DONE: "Done",
        }

        def update():
            alive, msgs = build_task.poll()
            for phase, typ, step, msg in msgs:
                if typ == Message.ENTER:
                    if phase == Phase.DONE:
                        self.progress.move()
                        continue
                    status = status_texts.get(phase, "Something")
                    self.console.write("Started {} phase".format(status.lower()), 'phase')
                    self.set_status("{}…".format(status))
                elif typ == Message.START_STEP:
                    step += 1
                    self.progress.move()
                    status = status_texts.get(phase, "Something")
                    self.console.write("{} step {}".format(status, step), 'step')
                    self.set_status("{} step {}/{}…".format(status, step, steps))
                elif typ == Message.MANAGER_MSG:
                    self.console.write(msg, 'manager')
                elif typ == Message.CONTAINER_MSG:
                    self.console.write(msg)
            if alive:
                self.master.after(UPDATE_INTERVAL, update)
            else:
                result = build_task.join()
                self.console.write(result, 'phase' if result.ok else 'error')
                self.set_status(result)
                self.unlock()
        update()

    def quit(self):
        if self.build_task and self.build_task.is_alive():
            self.build_task.join()
            self.build_task = None
        self.store()
        self.master.destroy()


def main():
    root = tk.Tk()
    gui = Roman(root)
    root.mainloop()


if __name__ == '__main__':
    main()
