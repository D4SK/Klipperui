#!/usr/bin/env python3
from setuptools import setup
from Cython.Build import cythonize

setup(ext_modules = cythonize("./*.pyx", compiler_directives={'language_level': "3"}))


# usage:
# cd klipperui
# source klippy-environment/bin/activate
# python3 ./klippy/setup.py build_ext --inplace
