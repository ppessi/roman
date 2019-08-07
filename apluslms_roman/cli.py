import argparse
import logging
from collections import namedtuple
from functools import partial
from glob import glob
from itertools import chain
from os import chdir, getcwd
from os.path import abspath, expanduser, expandvars, join as path_join
from sys import exit as _exit, stderr, stdout

from apluslms_yamlidator.document import Document
from apluslms_yamlidator.utils.yaml import rt_dump as yaml_dump
from apluslms_yamlidator.validator import ValidationError, render_error

from . import __version__
from .builder import Engine
from .configuration import ProjectConfig, ProjectConfigError
from .observer import StreamObserver
from .settings import GlobalSettings
from .utils.env import EnvDict, EnvError
from .utils.translation import _


LOG_LEVELS = [logging.WARNING, logging.INFO, logging.DEBUG]
logger = logging.getLogger(__name__)


def exit(status=None, message=None):
    # NOTE: always call with positional arguments only!
    # after py3.8: def exit(status=None, message=None, /):
    if message:
        print(message, file=stderr)
    logging.shutdown()
    _exit(status or 0)


def warning(message):
    print(_("WARNING: %s") % (message,), file=stderr)


_ActionContext = namedtuple('ActionContext', ('parser', 'args', 'settings', 'action'))
class ActionContext(_ActionContext):
    @property
    def action_name(self):
        action = self.action
        while hasattr(action, 'func'):
            action = action.func
        return action.__name__

    def run(self):
        return self.action(self)


class CallbackSubParsersAction(argparse._SubParsersAction):
    def __init__(self, *args, callback_dest=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._callback_dest = callback_dest

    def add_parser(self, name, callback=None, **kwargs):
        parser = super().add_parser(name, **kwargs)
        if callback:
            parser.set_defaults(**{self._callback_dest: callback})
        return parser

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        pass


class CallbackArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, callback_dest=None, callback_subparser_defaults=None, **kwargs):
        super().__init__(*args, **kwargs)

        if callback_dest is None:
            callback_dest = 'action'
        self.register('action', 'parsers',
            partial(CallbackSubParsersAction, callback_dest=callback_dest))
        self._callback_dest = callback_dest
        self._callback_subparsers = None
        if callback_subparser_defaults is not None:
            self._callback_subparser_defaults = callback_subparser_defaults.copy()
        else:
            self._callback_subparser_defaults = {}

    @property
    def help_callback(self):
        return self._callback_help_action

    def _callback_help_action(self, *args, **kwargs):
        self.print_help()
        self.exit()

    def get_callback(self, namespace):
        return getattr(namespace, self._callback_dest, None)

    def set_callback(self, callback):
        self.set_defaults(**{self._callback_dest: callback})

    def set_subparser_defaults(self, **kwargs):
        self._callback_subparser_defaults.update(kwargs)

    def use_subparsers(self, **kwargs):
        if self._callback_subparsers:
            raise RuntimeError
        for k, v in self._callback_subparser_defaults.items():
            kwargs.setdefault(k, v)
        kwargs.setdefault('parser_class',
            partial(type(self),
                    callback_dest=self._callback_dest,
                    callback_subparser_defaults=self._callback_subparser_defaults))
        self._callback_subparsers = subparsers = self.add_subparsers(**kwargs)
        if self.get_default(self._callback_dest) is None:
            self.set_callback(self.help_callback)
        return subparsers

    def add_parser(self, *args, **kwargs):
        if not self._callback_subparsers:
            raise RuntimeError
        return self._callback_subparsers.add_parser(*args, **kwargs)

    def copy_defaults_to(self, parser):
        # NOTE: uses undocumented private API
        defaults = {}

        # add any action defaults that aren't present
        for action in self._actions:
            if action.dest is not argparse.SUPPRESS and action.default is not argparse.SUPPRESS:
                defaults.setdefault(action.dest, action.default)

        # add any parser defaults that aren't present
        for dest in self._defaults:
            defaults.setdefault(dest, self._defaults[dest])

        if defaults:
            parser.set_defaults(**defaults)


## The command-line interface

# a basic parser interface

def configure_logging():
    logging.basicConfig(
        level=LOG_LEVELS[0],
        format="%(asctime)-15s %(levelname)-8s %(name)s: %(message)s",
    )


def create_parser(version=__version__,
                  **kwargs):
    # the parser
    kwargs.setdefault('description', _("A project material builder"))
    #parser = argparse.ArgumentParser(**kwargs)
    parser = CallbackArgumentParser(**kwargs)

    # basic options
    parser.add_argument('-V', '--version',
        action='version',
        version="%%(prog)s %s" % (version,),
        help=_("print a version info and exit"))
    parser.add_argument('-v', '--verbose',
        action='count',
        default=0,
        help=_("show some logged messages; repeat to show more"))
    parser.add_argument('--debug',
        action='store_true',
        help=_("show all logged messages"))
    # setting file support

    # global roman settings (user settings)
    parser.add_argument('-c', '--config',
        metavar=_('FILE'),
        help=_("use FILE as the global roman settings"))

    # project location and project settings
    parser.add_argument('-C', '--directory',
        metavar=_('DIR'),
        default=[],
        action='append',
        help=_(
            "Change to directory DIR before reading the configuration "
            "file or doing anything else. If specified multiple times, "
            "then values are joined with `os.path.join`."))
    parser.add_argument('-f', '--file',
        dest='project_config',
        metavar=_('FILE'),
        help=_("use the FILE as the project configuration file"))

    GlobalSettings.populate_parser(parser)

    return parser


def parse_actioncontext(parser, *, args=None):
    args = parser.parse_args(args=args)

    # set logging level
    max_level = len(LOG_LEVELS)-1
    if args.debug or args.verbose >= max_level:
        args.debug = True
        args.verbose = max_level
    logging.getLogger().setLevel(LOG_LEVELS[args.verbose])

    # set working directory
    if args.directory:
        dirs = [expanduser(expandvars(d)) for d in args.directory]
        chdir(path_join(*dirs))

    # load settings
    config = args.config if args.config is not None else GlobalSettings.get_config_path()

    logger.debug(_("Loading settings from '%s'"), config)

    try:
        settings = GlobalSettings.load(config, allow_missing=True)
    except ValidationError as e:
        exit(1, '\n'.join(render_error(e)))
    except OSError as e:
        exit(1, str(e))

    if args.config is not None and not settings.container.exists():
        warning(_("File {} doesn't exist.").format(args.config))

    settings.update_from_namespace(args)

    # post process rest of the args
    if args.steps and any(step == '?' for step in args.steps):
        args.list_steps = True

    return ActionContext(parser, args, settings, parser.get_callback(args))


# parser configuration for roman cli

def add_cli_actions(parser):
    def add_env_args(env):
        env.add_argument('-d', '--delete', action='append',
            help=_("delete value from environment"))
        env.add_argument('-s', '--set', action='append',
            help=_("set value in environment (deletes other values "
                "with same key, use --add to keep earlier values)"))
        env.add_argument('-a', '--add', action='append',
            help=_("add an item to the environment"))
        env.add_argument('--unset', action='store_true',
            help=_("when deleting values also mark them as unset"))
        env.add_argument('--force', action='store_true',
            help=_("when deleting values also delete any dicts that "
                "mark them as unset"))

    parser.set_subparser_defaults(
        metavar=_('COMMAND'),
        help=_("a list of sub commands:"))
    parser.use_subparsers(
        title=_("Commands"),
        description=_(
            "Top level commands. Check the per command help "
            "with `%(prog)s COMMAND --help`."))

    build = parser.add_parser('build', aliases=['b'],
        callback=build_action,
        help=_("build the project (default action)"))
    build.add_argument('--clean', action='store_true',
        help=_("delete old build files before building"))
    build.add_argument('-s', '--steps', nargs='+',
        help=_("select which steps to build and in which order (use either index or step name)"))
    build.add_argument('--no-color', action='store_true',
        help=_("print output with no colors"))

    # build is the default callback. set defaults for it
    build.copy_defaults_to(parser)
    parser.set_callback(build_action)


    parser.add_parser('init',
        callback=init_action,
        help=("create roman settings file in current directory"))


    config = parser.add_parser('config', aliases=['c'],
        callback=config_print_action,
        help=_("manage %(prog)s configuration"))

    config.add_argument('-g', '--global', action='store_true', dest='global_',
        help=_("limit actions to the global %(prog)s configuration"))

    config.add_argument('-p', '--project', action='store_true',
        help=_("limit actions to the project configuration"))

    with config.use_subparsers(title=_("Config actions")):
        config.add_parser('print', aliases=['p'],
            callback=config_print_action,
            help=_("print configurations (default action)"))

        env = config.add_parser('env', aliases=['e'],
            callback=config_env_action,
            help=_("actions related to environment. default: print environment "
                "with variables extended"))
        add_env_args(env)

        setval = config.add_parser('set', aliases=['s'],
            callback=config_set_action,
            help=_("change/add values in the configuration"))
        setval.add_argument('values', nargs='+', help=_("format: key=val"))

        delval = config.add_parser('remove', aliases=['rm'],
            callback=config_rm_action,
            help=_("remove values from the configuration"))
        delval.add_argument('keys', nargs='+')


    step = parser.add_parser('step',
        help=_("actions for editing steps in the project configuration"))

    with step.use_subparsers(title=_("Step actions")):
        step.add_parser('list', aliases=['ls'],
            callback=step_list_action,
            help=_("list all steps and exit"))

        add = step.add_parser('add', aliases=['a'],
            callback=step_add_action,
            help=_("add step to project config"))
        add.add_argument('img')
        add.add_argument('-n', '--name')
        add.add_argument('-e', '--env', nargs='+', help=_("format: key=val"))
        add.add_argument('-c', '--cmd')
        add.add_argument('-m', '--mnt')

        remove = step.add_parser('remove', aliases=['rm'],
            callback=step_rm_action,
            help=_("remove a step from project config"))
        remove.add_argument('ref', help=_("ref can be either step index or name"))
        remove.add_argument('-f', '--force', action='store_true',
            help=_("delete step without confirmation"))

        env = step.add_parser('env', callback=step_env_action,
            help=_("actions related to step environments. default: print env"))
        add_env_args(env)
        env.add_argument('ref', help=_("ref can be either step index or name"))


    validate = parser.add_parser('validate',
        help=_("validatation actions for debuging"))

    with validate.use_subparsers(title=_("Validate actions")):
        validate_schema = validate.add_parser('schema',
            callback=validate_schema_action,
            help=_("validate an YAML against a known schema"))
        validate_schema.add_argument('-v', '--version', metavar='version',
            dest='schema_version',
            help=_(
                "the version of a schema for validation "
                "(read from 'version' in a document by default)"))
        validate_schema.add_argument('schema_name', metavar='schema',
            help=_("the name of a schema for validation"))
        validate_schema.add_argument('data_files', metavar='file', nargs='+',
            help=_("a YAML/JSON file(s) to be validated"))

    backend = parser.add_parser('backend',
        help=_("backend actions"))
    with backend.use_subparsers(title=_("Backend actions")):
        backend.add_parser('test',
            callback=backend_test_action,
            help=_("test the backend connection"))
        backend.add_parser('info',
            callback=partial(backend_test_action, verbose=True),
            help=_("show the backend information"))
        clean = backend.add_parser('clean',
            callback=backend_clean_action,
            help=_("remove all Roman related data from the backend"))
        clean.add_argument('-f', '--force', action='store_true',
            help=_("also remove containers that are younger than a day "
                "or don't have an expire label"))

    return parser


def main(*, args=None):
    """
    CLI main function:
    1. parse arguments
    2. execute action with the arguments
    3. exit with the code from the action
    """
    configure_logging()
    parser = create_parser()
    add_cli_actions(parser)
    context = parse_actioncontext(parser, args=args)

    logger.debug(_("Executing action %s"), context.action_name)
    exit(context.run())



## Actions

# action utils

def get_engine(context):
    try:
        return Engine(settings=context.settings)
    except ImportError:
        exit(1, _("ERROR: Unable to find backend '{}'.").format(
            context.settings.get('backend', 'docker')))


def get_config(context):
    try:
        if context.args.project_config:
            project_config = abspath(expanduser(expandvars(context.args.project_config)))
            return ProjectConfig.load_from(project_config)
        try:
            return ProjectConfig.find_from(getcwd())
        except FileNotFoundError as err:
            exit(1, str(err) + _("\nYou can create a configuration file with 'roman init'."))
    except ValidationError as e:
        exit(1, '\n'.join(render_error(e)))
    except ProjectConfigError as e:
        exit(1, _("Invalid project configuration: {}").format(e))


# for situations where env is given by the user
def check_env(env):
    if not env:
        return
    if any('=' not in string for string in env):
        exit(1, "Please give env values in 'key=var' format")


# TODO?: if file has been 'edited' but values haven't
# changed, the outcome is 'file successfully edited'
def report_save(output):
    if output == Document.SaveOutput.NO_SAVE:
        print("No changes in the file.")
    elif output == Document.SaveOutput.SAVED_CHANGES:
        print("File successfully edited.")
    else:
        print("File created.")


def verify_engine(engine, only_when_error=False):
    error = engine.verify()
    if error:
        print(_("Container backend connection failed.\nUsing driver %s.%s.\n\n%s") % (
            engine.backend.__module__,
            engine.backend.__class__.__name__,
            error,
        ))
        if hasattr(engine.backend, 'debug_hint'):
            print("\n" + engine.backend.debug_hint)
        return False
    elif not only_when_error:
        print(_("Container backend connected successfully.\nUsing driver %s.%s.") % (
            engine.backend.__module__,
            engine.backend.__class__.__name__,
        ))
    return True


def get_project_environment(context, config=None):
    if config is None:
        config = get_config(context)
    return EnvDict(
        (context.settings.mlget('environment', []), 'global settings'),
        (config.mlget('environment', []), 'project configuration')
    )


def get_step(config, ref):
    try:
        return config.get_step(ref)
    except IndexError:
        exit(1, _(
            "Index is out of range. Remember, indexing start from 0. "
            "Use 'roman --list-steps' to see step indexes."))
    except KeyError:
        exit(1, _("There is no step called '{}'").format(ref))


def select_config(context):
    if context.args.global_ and context.args.project:
        exit(1, "Choose either global settings or project settings")
    return context.settings if context.args.global_ else get_config(context)


def env_set(env, args):
    if not args:
        return
    check_env(args)
    for arg in args:
        key, _, val = arg.partition('=')
        env.set_in_env('', key, val)


def env_delete(env, context):
    if not context.args.delete:
        return
    unset = context.args.unset
    delete_unset = context.args.force
    if unset and delete_unset:
        exit(1, _("Please only pick one of the 'unset' and 'force' options"))
    not_deleted = []
    for key in context.args.delete:
        deleted = env.delete_from_env('', key, delete_unset=delete_unset)
        if unset and {'name': key, 'unset': True} not in env.get_env(''):
            env.add_to_env('', {'name': key, 'unset': True})
        elif not deleted:
            not_deleted.append(key)
    if not_deleted and not unset:
        if len(not_deleted) == 1:
            beginning = _("Key {} was not found in env").format(not_deleted[0])
        else:
            beginning = (_("Keys {} were not found in env")
                .format(', '.join(not_deleted[0])))
        if delete_unset:
            print(beginning + _(" and couldn't be deleted. Use --unset to "
                "mark values as unset"))
        else:
            print(beginning + _(" or had the value 'unset'. "
                "Use --force to delete dicts marking values as unset "
                "and use --unset to mark values as unset"))


def env_add(env, args):
    if not args:
        return
    check_env(args)
    for arg in args:
        env.add_to_env('', arg)


# actions

def build_action(context):
    config = get_config(context)
    engine = get_engine(context)
    observer = StreamObserver(colors=(not context.args.no_color))
    builder = engine.create_builder(config, observer=observer,
        environment=get_project_environment(context, config))

    if hasattr(context.args, 'list_steps') and context.args.list_steps:
        step_list_action(context)
        return 0

    if not verify_engine(engine, only_when_error=True):
        return 1
    if not config.steps:
        print("Nothing to build.")
        return 1

    # build project
    steps = context.args.steps
    if steps:
        steps = chain.from_iterable(step.split(',') for step in steps)

    try:
        result = builder.build(step_refs=steps, clean_build=context.args.clean)
    except KeyError as err:
        exit(1, _("No step named {}.").format(err.args[0]))
    except IndexError as err:
        exit(1,
            _("Index {} is out of range. There are {} steps. Indexing begins ar 0.")
            .format(err.args[0], len(config.steps)))
    except EnvError as err:
        exit(1, str(err))

    return result.code


def init_action(context):
    project_config = context.args.project_config
    try:
        if project_config:
            config = ProjectConfig.load_from(project_config)
        else:
            config = ProjectConfig.find_from(getcwd())
        exit(1, _("A project configuration already exists at {}".format(config.path)))
    except FileNotFoundError:
        pass

    if project_config:
        is_recognized = False
        if '.' in project_config:
            name, prefix = project_config.rsplit('.', 1)
            is_recognized = (
                name in ProjectConfig.DEFAULT_NAMES and
                prefix in ProjectConfig.DEFAULT_PREFIXES
            )

        if not is_recognized:
            # ensure expanded and absolute path
            project_config = abspath(expanduser(expandvars(project_config)))
            warning(_(
                "roman won't recognize {} as a project config file without the -f flag"
            ).format(project_config))
    else:
        project_config = ProjectConfig.DEFAULT_FILENAME
    ProjectConfig.load(project_config, allow_missing=True).save()
    print(_("Project configuration file {} created successfully.".format(project_config)))


def config_print_action(context):
    all_ = not any(getattr(context.args, k, False) for k in ('global_', 'project'))
    if context.args.debug:
        print("---\n# arguments:")
        data = {k: v for k, v in vars(context.args).items() if k[0] != '_' and not callable(v)}
        yaml_dump(data, stdout)
    if all_ or context.args.global_:
        print("---\n# roman settings:")
        yaml_dump(context.settings._data, stdout)
    if all_ or context.args.project:
        print("---\n# project config")
        yaml_dump(get_config(context)._data, stdout)


def config_env_action(context):
    if not (context.args.delete or context.args.set or context.args.add):
        config_env_print(context)
        return
    config = select_config(context)
    env = EnvDict((config.get('environment', []), ''))
    env_delete(env, context)
    env_set(env, context.args.set)
    env_add(env, context.args.add)
    env = env.get_env('')
    # delete env if empty
    if not env and 'environment' in config:
        del config['environment']
    else:
        config['environment'] = env
    config.validate()
    config.save()


def config_env_print(context):
    if context.args.global_ and not context.args.project:
        env = EnvDict(
            (context.settings.get('environment', []), 'global settings'))
    else:
        env = get_project_environment(context)
    try:
        env = env.get_combined()
    except EnvError as err:
        exit(1, str(err))
    if not env:
        print("Environment is empty.")
    else:
        yaml_dump(env, stdout)


def config_set_action(context):
    document = select_config(context)

    for val in context.args.values:
        try:
            key, val = val.split('=', 1)
            document.mlset_cast(key, val)
        except IndexError as err:
            if hasattr(err, 'index'):
                exit(1, _("Index {} is out of range").format(err.index))
            exit(1, str(err))
        except ValueError as err:
            if hasattr(err, 'value_type'):
                exit(1, _("{} should be of type '{}', but was '{}'.").format(
                    key, err.value_type, type(val).__name__))
            exit(1, _("Give values in format 'key=val'."))

    document.validate()
    report_save(document.save())


def config_rm_action(context):
    document = select_config(context)

    if not document.container.exists():
        print("Cannot delete from config because config file doesn't exist.")
        exit(0)

    for val in context.args.keys:
        try:
            document.mldel(val)
        except KeyError:
            print("Key {} doesn't exist in config.".format(val))

    document.validate()
    report_save(document.save())


def step_list_action(context):
    steps = get_config(context).steps
    if not steps:
        print("The project config has no steps.")
        return
    steps = [{'img': step} if isinstance(step, str) else step for step in steps]
    num_len = max(2, len(str(len(steps)-1)))
    name_len = max(4, max(len(s.get('name', '')) for s in steps))
    header_fmt = "{:>%ds}  {:%ds} {}" % (num_len, name_len)
    step_fmt = "{:%dd}. {:%ds} {}" % (num_len, name_len)
    print(header_fmt.format('ID', 'NAME', 'IMAGE'))
    for i, step in enumerate(steps):
        print(step_fmt.format(i, step.get('name', ''), step['img']))


def step_add_action(context):
    args = context.args
    env = args.env
    check_env(env)
    step = {
        'img': args.img,
        'cmd': args.cmd,
        'mnt': args.mnt,
        'env': env,
        'name': args.name
    }
    step = {k: v for k, v in step.items() if v}
    config = get_config(context)
    try:
        config.add_step(step)
    except ValueError as err:
        exit(1, str(err))
    try:
        config.validate()
    except ValidationError as err:
        exit(1, '\n'.join(render_error(err)))
    config.save()
    print("Step successfully added to config.")


def step_rm_action(context):
    def confirm_del(step):
        print('step:')
        print('  {}'.format(yaml_dump(step.get_data()).replace('\n', '\n  ')))
        while True:
            i = input("Delete this step? (y/n) ").lower()
            if i == 'y':
                return True
            if i == 'n':
                return False

    config = get_config(context)
    step = get_step(config, context.args.ref)
    if not context.args.force and not confirm_del(step):
        return
    config.del_step(step)
    config.validate()
    config.save()
    print("Step successfully removed from config.")


def step_env_action(context):
    config = get_config(context)
    step = get_step(config, context.args.ref)
    if not (context.args.delete or context.args.set or context.args.add):
        step_env_print(context, config, step)
        return
    if not isinstance(step, str):
        env = EnvDict((step.get('env', []), ''))
        is_str = False
    else:
        step = {'img': step}
        is_str = True
        env = EnvDict(([], ''))
    env_delete(env, context)
    env_set(env, context.args.set)
    env_add(env, context.args.add)
    #if env is empty, delete it
    env = env.get_env('')
    if not env and 'env' in step:
        del step['env']
    else:
        step['env'] = env
    if is_str and env:
        config.steps[config.ref_to_index(context.args.ref)] = step
    config.validate()
    config.save()


def step_env_print(context, config, step):
    try:
        env = get_project_environment(context, config)
        if not isinstance(step, str):
            env.add_env(
                step.get('env', []), 'step {}'.format(
                    step.get('name', context.args.ref)))
        env = env.get_combined()
        if not env:
            print("Step environment is empty.")
        else:
            yaml_dump(env, stdout)
    except EnvError as err:
        exit(1, str(err))


def validate_schema_action(context):
    Container = Document.bind(schema=context.args.schema_name).Container
    files = chain.from_iterable(glob(s) for s in context.args.data_files)
    max_version = context.args.schema_version

    def print2(msg):
        print(" ", msg)

    if max_version is None:
        get_docs = lambda x: x
    else:
        def get_docs(container):
            try:
                doc = container.get_latest(max_version, validate=False)
            except KeyError:
                print2(_("No elements with max version %s found!") % max_version)
                return ()
            if doc.version is None:
                doc.version = max_version
            return (doc,)

    errors = 0
    documents = 0
    for file_ in files:
        container = Container(file_)
        print("%s:" % (container.path,))
        if not container:
            print2(_("The data file is empty!"))
            continue

        for doc in get_docs(container):
            try:
                doc.validate(quiet=True)
            except ValidationError as e:
                print2(_("Document %d failed against %s") % (doc.index, doc.validator_id))
                print('\n'+'\n'.join('    '+s for s in render_error(e))+'\n')
                errors += 1
            else:
                print2(_("Document %d validates against %s") % (doc.index, doc.validator_id))
            documents += 1
        print()
    if errors > 0:
        print(_("Found total of %d errors in %d documents.") % (errors, documents))
        return 1
    print(_("All %d documents are valid.") % documents)
    return 0


def backend_test_action(context, verbose=False):
    engine = get_engine(context)
    if not verify_engine(engine):
        return 1
    if verbose:
        print('\n')
        print(engine.version_info())
    return 0


def backend_clean_action(context):
    engine = get_engine(context)
    engine.backend.cleanup(context.args.force)


if __name__ == '__main__':
    main()
