import sys

from configparser import ConfigParser
from os.path import abspath, dirname, join as path_join
from time import time

from dulwich.errors import NotGitRepository
from dulwich.repo import Repo


# FIXME: is bytes okay or do the values need to be strings?
# what should empty values be, is None okay or should the keys not exist at all?
def get_drone_environment(build_folder):
    env = {}
    build_folder = abspath(build_folder)
    try:
        while True:
            try:
                repo = Repo(build_folder)
                break
            except NotGitRepository:
                new_folder = dirname(build_folder)
                if new_folder == build_folder:
                    raise
                build_folder = new_folder
        branch_ref = repo.refs.follow(b'HEAD')[0][-1]
        branch = branch_ref.rpartition(b'/')[2]
        commit = repo[repo.head()]
        author_name, _, author_email = commit.author.rpartition(b' ')
        author_email = author_email[1:-1]

        parents = commit.parents
        # FIXME: do we care which parent is selected if there are several?
        parents = parents[0] if parents else None

        c = ConfigParser()
        c.read(path_join(repo.commondir(), 'config'))
        # FIXME: is 'remote "origin"' always there? if it isn't, can there be
        # any other remotes (e.g. upstream)?
        ssh_url = c['remote "origin"']['url']
        repo_name = ssh_url.partition(':')[2].rpartition('.')[0]
        http_url = 'https://github.com/' + repo_name
        commit_url = http_url + '/commit/' + commit.id.decode('utf-8')

        env.update({
            'DRONE_BRANCH': branch,
            'DRONE_BUILD_EVENT': 'push',
            'DRONE_COMMIT_AFTER': commit.id,
            # 'DRONE_COMMIT_AUTHOR': None,
            # 'DRONE_COMMIT_AUTHOR_AVATAR': None,
            'DRONE_COMMIT_AUTHOR_EMAIL': author_email,
            'DRONE_COMMIT_AUTHOR_NAME': author_name,
            'DRONE_COMMIT_BEFORE': parents,
            'DRONE_COMMIT_BRANCH': branch,
            'DRONE_COMMIT_LINK': commit_url,
            'DRONE_COMMIT': commit.id,
            'DRONE_COMMIT_MESSAGE': commit.message,
            'DRONE_COMMIT_REF': branch_ref,
            'DRONE_COMMIT_SHA': commit.id,
            'DRONE_GIT_HTTP_URL': http_url + '.git',
            'DRONE_GIT_SSH_URL': ssh_url,
            'DRONE_REPO': repo_name,
            # 'DRONE_REPO_BRANCH': None,
            'DRONE_REMOTE_URL': ssh_url,
            'DRONE_REPO_LINK': http_url,
            'DRONE_REPO_NAME': repo_name.rpartition('/')[2],
            'DRONE_REPO_NAMESPACE': repo_name.rpartition('/')[0],
            # 'DRONE_REPO_PRIVATE': None,
            'DRONE_REPO_SCM': 'git',
            # 'DRONE_REPO_VISIBILITY': None,
        })
    except NotGitRepository:
        pass

    env.update({
        'DRONE': True,
        # difference between started and created? should these be filled later?
        'DRONE_BUILD_CREATED': int(time()),
        # 'DRONE_BUILD_NUMBER': None,
        'DRONE_BUILD_STARTED': int(time())
        # 'DRONE_MACHINE': None,
        # 'DRONE_RUNNER_HOST': None,
        # 'DRONE_RUNNER_HOSTNAME': None,
        # 'DRONE_RUNNER_PLATFORM': None,
        # 'DRONE_SYSTEM_HOST': None,
        # 'DRONE_SYSTEM_HOSTNAME': None,
        # 'DRONE_RUNNER_LABEL': None,
        # 'DRONE_SYSTEM_VERSION': None,
        # 'DRONE_TAG': None,
        })
    longest = max((len(key) for key in env.keys()))
    print_str = "{0: <%d}  {1}" % longest
    for key in sorted(env.keys()):
        if isinstance(env[key], bytes):
            env[key] = env[key].decode('utf-8')
        print(print_str.format(key, env[key]))


"""

git pr:
DRONE_PULL_REQUEST
DRONE_SOURCE_BRANCH
DRONE_TARGET_BRANCH


"""

def main():
    if len(sys.argv) > 1:
        get_drone_environment(sys.argv[1])
    else:
        get_drone_environment('.')

main()
