from pathlib import PurePosixPath
import json
import logging
import re
from os import environ


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
json_re = re.compile(r'^(?:["[{]|(?:-?[1-9]\d*(?:\.\d+)?|null|true|false)$)')


def get_host_path(original, mapping):
    ret = original
    orig_path = PurePosixPath(original)
    for k, v in mapping.items():
        try:
            logger.debug("Mapping:{}:{}".format(k, v))
            relative_path = orig_path.relative_to(k)
            ret = PurePosixPath(v).joinpath(relative_path)
            return str(ret)
        except ValueError:
            logger.critical("Error when composing new path!")
            pass
    return str(ret)


def get_pair_form_env(key, json_str, read_key=None):
    if key == read_key:
        try:
            ret = json.loads(json_str)
            if isinstance(ret, dict):
                return ret
        except json.decoder.JSONDecodeError:
            logger.critical("Error, check your json string")
    return json_str


def env_value_to_dict(json_str):
    if json_re.match(json_str):
        try:
            ret = json.loads(json_str)
            return ret
        except json.decoder.JSONDecodeError:
            logger.critical("Error, check your json string")
    return json_str


def nest_dict(flat_dict, sep):
    ret = {}
    for k, v in flat_dict.items():
        key_list = k.split(sep, 1)
        if len(key_list) == 2:
            root = key_list[0]
            if root not in ret:
                ret[root] = {}
            ret[root][key_list[1]] = v
        else:
            ret[k] = v
    return ret


def load_from_env(env_prefix=None, sep=None, decode_json=True):
    if decode_json:
        decode = lambda s: json.loads(s) if json_re.match(s) is not None else s
    else:
        decode = lambda s: s
    env = {k[len(env_prefix):].lower(): decode(v) for k, v in environ.items() if k.startswith(env_prefix)}
    if sep is not None:
        env = nest_dict(env, sep)
    return env