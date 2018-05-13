#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
"""A setuptools based setup module.

See:
https://packaging.python.org/en/latest/distributing.html
https://github.com/pypa/sampleproject
"""

from setuptools import setup, find_packages
# To use a consistent encoding
from codecs import open
from datetime import datetime
from os import path
from sys import platform

here = path.abspath(path.dirname(__file__))

# Get the long description from the README file
with open(path.join(here, 'README.rst'), encoding='utf-8') as f:
    long_description = f.read()


options = dict(
    name='apluslms-roman-tki',
    version='0.1.0',
    description='Course material builder for online learning systems (tkinter gui)',
    long_description=long_description,
    keywords='apluslms material',
    url='https://github.com/apluslms/roman',
    author='Jaakko Kantojärvi',
    author_email='jaakko.kantojarvi@aalto.fi',
    license='MIT',

    # See https://pypi.python.org/pypi?%3Aaction=list_classifiers
    classifiers=[
        #   3 - Alpha
        #   4 - Beta
        #   5 - Production/Stable
        'Development Status :: 4 - Beta',
        'License :: OSI Approved :: MIT License',

        'Intended Audience :: Developers',
        'Operating System :: OS Independent',
        'Topic :: Software Development :: Build Tools',
        'Topic :: Software Development :: Libraries :: Python Modules',

        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3 :: Only',
    ],

    zip_safe=True,
    #packages=find_packages(exclude=['contrib', 'docs', 'tests']),
    py_modules=['roman_tki'],
    include_package_data = True,
    package_data={
        '': ['*.json'],
    },

    install_requires=[
        'apluslms-roman >= 0.1.0',
        'appdirs >= 1.4.0',
    ],

    entry_points={
        'console_scripts': [
            'roman-tki = roman_tki:main',
        ],
    },
)


if platform == 'darwin':
    APP_NAME = 'Roman'
    options.update(dict(
        app=['roman_tki.py'],
        app_name=APP_NAME,
    ))
    options.setdefault('options', {})['py2app'] = {
        'bdist_base': '../build',
        'dist_dir': '../dist',
        'force_system_tk': True,
        'iconfile': 'roman.icns',
        'plist': {
            'CFBundleName': APP_NAME,
            'CFBundleDisplayName': APP_NAME,
            'CFBundleGetInfoString': options['description'],
            'CFBundleIdentifier': "io.github.apluslms.Roman.roman_tki",
            'CFBundleVersion': "1.0",
            'CFBundleShortVersionString': options['version'],
            'NSHumanReadableCopyright': u"Copyright © {}, {}".format(
                datetime.now().year,
                options['author'],
            ),
        },
    }
    options.setdefault('setup_requires', []).extend([
        'py2app >= 0.12, != 0.14',
    ])

if __name__ == '__main__':
    setup(**options)
