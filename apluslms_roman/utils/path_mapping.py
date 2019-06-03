from pathlib import PurePosixPath


def get_host_path(original, mapping):
    ret = original
    orig_path = PurePosixPath(original)
    for k, v in mapping.items():
        print(k, v)
        try:
            relative_path = orig_path.relative_to(k)
            ret = PurePosixPath(v).joinpath(relative_path)
            print("Get new path:", ret)
            return str(ret)
        except ValueError:
            pass
    return str(ret)

