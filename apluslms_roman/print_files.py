from json import dumps
from os import listdir
from os.path import getmtime, getsize, isdir, join

def main():
    file_info = {}

    def get_file_info(path):
        files = [join(path, f) for f in sorted(listdir(path)) if f[0] != '.']
        if not files:
            file_info[path] = (int(getmtime(path)), getsize(path))
        for f in files:
            if 'file_manifest.json' in f:
                continue
            if not isdir(f):
                file_info[f] = (int(getmtime(f)), getsize(f))
            else:
                get_file_info(f)

    get_file_info('.')

    with open('file_manifest.json', 'w') as f:
        f.write(dumps(file_info))

if __name__ == '__main__':
    main()
