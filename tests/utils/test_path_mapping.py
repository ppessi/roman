import unittest
from json import loads, dumps
from apluslms_roman.utils.path_mapping import json_re, env_value_to_dict

test_case_loadable = (
    'true',
    'false',
    'null',
    '123',
    '-123',
    '3.14',
    '-3.14',
    '{"foo": "bar"}',
    '[1, 2, 3]',
    '"foo bar"'
)

test_case_not_loadable = (
    "/foobar.py",
    "text",
    "yes",
    "0123123",
)


class TestJsonLoadable(unittest.TestCase):

    def test_loadable_not_raise(self):
        for i, case in enumerate(test_case_loadable):
            with self.subTest(i=i):
                loads(case)

    def test_not_loadable_raise(self):
        for i, case in enumerate(test_case_not_loadable):
            with self.subTest(i=i):
                with self.assertRaises(ValueError, msg="Testing:{}".format(case)):
                    loads(case)


class TestJsonRegex(unittest.TestCase):

    def test_loadable_match(self):
        for i, case in enumerate(test_case_loadable):
            with self.subTest(i=i):
                self.assertTrue(json_re.match(case)is not None, msg="Testing:{}".format(case))

    def test_not_loadable_not_match(self):
        for i, case in enumerate(test_case_not_loadable):
            with self.subTest(i=i):
                self.assertFalse(json_re.match(case) is not None, msg="Testing:{}".format(case))


class TestValueDict(unittest.TestCase):

    def test_loadable_type(self):
        for i, case in enumerate(test_case_loadable):
            with self.subTest(i=i):
                self.assertEqual(env_value_to_dict(case), loads(case), msg="Testing:{}".format(case))

    def test_not_loadable_type(self):
        for i, case in enumerate(test_case_not_loadable):
            with self.subTest(i=i):
                self.assertEqual(env_value_to_dict(case), case, msg="Testing:{}".format(case))
