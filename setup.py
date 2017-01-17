#!/usr/bin/python
# -*- coding: utf-8 -*-

from setuptools import setup

setup(
    name='iframe-playlist-generator',
    version='0.1.1',
    author='Peter Norton',
    author_email='peter@nortoncrew.com',
    packages=['iframeplaylistgenerator'],
    url='https://github.com/nweber/iframe-playlist-generator',
    description='HLS I-frame playlist generator',
    long_description=open('README.rst').read(),
    install_requires=['m3u8_gzip>=0.4.0'],
    dependency_links=['https://github.com/nweber/m3u8-gzip/tarball/master#egg=m3u8_gzip-0.4.0']
)
