#!/usr/bin/env python3
"""Offline regression checks for Mongo resources inside application namespaces."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import checks


class MongoResources(unittest.TestCase):
    def setUp(self):
        self.pods = [
            {'metadata':{'name':'mongo-0','labels':{'app':'mongo-pilot'}},
             'spec':{'containers':[{'name':'mongo'}]}},
            {'metadata':{'name':'auth-123','labels':{'chart-app':'auth'}},
             'spec':{'containers':[{'name':'auth'}]}}
        ]
        self.services = [
            {'metadata':{'name':name},'spec':{'type':'ClusterIP','selector':{'app':'mongo-pilot'}}}
            for name in ('mongo','mongo-access')
        ] + [{'metadata':{'name':'auth'},'spec':{'type':'ClusterIP','selector':{'chart-app':'auth'}}}]
        self.owned = []
        def run(args, capture):
            self.assertTrue(capture)
            self.assertEqual(args[:3],['kubectl','-n','chart-test'])
            self.assertEqual(args[3], 'get')
            return json.dumps({'items':{'pods':self.pods,'services':self.services}[args[4]]})
        self.mongo = SimpleNamespace(NS='chart-test',run=run,owned=self.owned.append)

    def test_sibling_apps_are_allowed_and_only_mongo_ownership_is_checked(self):
        checks.mongo_resources(self.mongo)
        self.assertEqual([o['metadata']['name'] for o in self.owned],['mongo-0','mongo','mongo-access'])

    def test_multiple_mongo_members_are_rejected(self):
        extra = copy.deepcopy(self.pods[0])
        extra['metadata']['name'] = 'mongo-1'
        self.pods.append(extra)
        with self.assertRaisesRegex(AssertionError, 'exactly one Mongo'):
            checks.mongo_resources(self.mongo)

    def test_external_or_extra_mongo_service_is_rejected(self):
        self.services[0]['spec']['type'] = 'NodePort'
        with self.assertRaises(AssertionError):
            checks.mongo_resources(self.mongo)
        self.services[0]['spec']['type'] = 'ClusterIP'
        extra = copy.deepcopy(self.services[0])
        extra['metadata']['name'] = 'mongo-member-0'
        self.services.append(extra)
        with self.assertRaises(AssertionError):
            checks.mongo_resources(self.mongo)

    def test_container_and_init_container_limits_are_rejected(self):
        for kind in ('containers','initContainers'):
            with self.subTest(kind=kind):
                previous = copy.deepcopy(self.pods[0]['spec'])
                self.pods[0]['spec'][kind] = [{'name':'fixture','resources':{'limits':{'memory':'1Gi'}}}]
                with self.assertRaises(AssertionError):
                    checks.mongo_resources(self.mongo)
                self.pods[0]['spec'] = previous


if __name__ == '__main__':
    unittest.main()
