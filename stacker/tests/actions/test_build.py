import unittest
from collections import namedtuple

import mock

from stacker import exceptions
from stacker.actions import build
from stacker.actions.build import (
    _resolve_parameters,
    _handle_missing_parameters,
)
from stacker.blueprints.variables.types import CFNString
from stacker.context import Context, Config
from stacker.providers.base import BaseProvider


def mock_stack(parameters):
    return {
        'Parameters': [
            {'ParameterKey': k, 'ParameterValue': v} for k, v in
            parameters.items()
        ]
    }


class TestProvider(BaseProvider):
    def __init__(self, outputs=None, *args, **kwargs):
        self._outputs = outputs or {}

    def set_outputs(self, outputs):
        self._outputs = outputs

    def get_stack(self, stack_name, **kwargs):
        if stack_name not in self._outputs:
            raise exceptions.StackDoesNotExist(stack_name)
        return {"name": stack_name, "outputs": self._outputs[stack_name]}

    def get_outputs(self, stack_name, *args, **kwargs):
        stack = self.get_stack(stack_name)
        return stack["outputs"]


class TestBuildAction(unittest.TestCase):
    def setUp(self):
        self.context = Context(config=Config({"namespace": "namespace"}))
        self.build_action = build.Action(self.context, provider=TestProvider())

    def _get_context(self, **kwargs):
        config = Config({
            "namespace": "namespace",
            "stacks": [
                {"name": "vpc"},
                {"name": "bastion",
                    "variables": {
                        "test": "${output vpc::something}"}},
                {"name": "db",
                    "variables": {
                        "test": "${output vpc::something}",
                        "else": "${output bastion::something}"}},
                {"name": "other", "variables": {}}
            ],
        })
        return Context(config=config, **kwargs)

    def test_handle_missing_params(self):
        stack = {'StackName': 'teststack'}
        def_params = {"Address": "192.168.0.1"}
        required = ["Address"]
        result = _handle_missing_parameters(def_params, required, stack)
        self.assertEqual(result, def_params.items())

    def test_gather_missing_from_stack(self):
        stack_params = {"Address": "10.0.0.1"}
        stack = mock_stack(stack_params)
        def_params = {}
        required = ["Address"]
        self.assertEqual(
            _handle_missing_parameters(def_params, required, stack),
            stack_params.items())

    def test_missing_params_no_stack(self):
        params = {}
        required = ["Address"]
        with self.assertRaises(exceptions.MissingParameterException) as cm:
            _handle_missing_parameters(params, required)

        self.assertEqual(cm.exception.parameters, required)

    def test_stack_params_dont_override_given_params(self):
        stack_params = {"Address": "10.0.0.1"}
        stack = mock_stack(stack_params)
        def_params = {"Address": "192.168.0.1"}
        required = ["Address"]
        result = _handle_missing_parameters(def_params, required, stack)
        self.assertEqual(result, def_params.items())

    def test_generate_plan(self):
        context = self._get_context()
        build_action = build.Action(context)
        plan = build_action._generate_plan()
        self.assertEqual(
            {
                'namespace-db': set(['namespace-bastion', 'namespace-vpc']),
                'namespace-bastion': set(['namespace-vpc']),
                'namespace-other': set([]),
                'namespace-vpc': set([])},
            plan.graph.dag.graph
        )

    def test_dont_execute_plan_when_outline_specified(self):
        context = self._get_context()
        build_action = build.Action(context)
        with mock.patch.object(build_action, "_generate_plan") as \
                mock_generate_plan:
            build_action.run(outline=True)
            self.assertEqual(mock_generate_plan().execute.call_count, 0)

    def test_execute_plan_when_outline_not_specified(self):
        context = self._get_context()
        build_action = build.Action(context)
        with mock.patch.object(build_action, "_generate_plan") as \
                mock_generate_plan:
            build_action.run(outline=False)
            self.assertEqual(mock_generate_plan().execute.call_count, 1)

    def test_should_update(self):
        test_scenario = namedtuple("test_scenario",
                                   ["locked", "force", "result"])
        test_scenarios = (
            test_scenario(locked=False, force=False, result=True),
            test_scenario(locked=False, force=True, result=True),
            test_scenario(locked=True, force=False, result=False),
            test_scenario(locked=True, force=True, result=True)
        )
        mock_stack = mock.MagicMock(["locked", "force", "name"])
        mock_stack.name = "test-stack"
        for t in test_scenarios:
            mock_stack.locked = t.locked
            mock_stack.force = t.force
            self.assertEqual(build.should_update(mock_stack), t.result)

    def test_should_submit(self):
        test_scenario = namedtuple("test_scenario",
                                   ["enabled", "result"])
        test_scenarios = (
            test_scenario(enabled=False, result=False),
            test_scenario(enabled=True, result=True),
        )

        mock_stack = mock.MagicMock(["enabled", "name"])
        mock_stack.name = "test-stack"
        for t in test_scenarios:
            mock_stack.enabled = t.enabled
            self.assertEqual(build.should_submit(mock_stack), t.result)


class TestFunctions(unittest.TestCase):
    """ test module level functions """

    def setUp(self):
        self.ctx = Context({"namespace": "test"})
        self.prov = mock.MagicMock()
        self.bp = mock.MagicMock()

    def test_resolve_parameters_unused_parameter(self):
        self.bp.get_parameter_definitions.return_value = {
            "a": {
                "type": CFNString,
                "description": "A"},
            "b": {
                "type": CFNString,
                "description": "B"}
        }
        params = {"a": "Apple", "c": "Carrot"}
        p = _resolve_parameters(params, self.bp)
        self.assertNotIn("c", p)
        self.assertIn("a", p)

    def test_resolve_parameters_none_conversion(self):
        self.bp.get_parameter_definitions.return_value = {
            "a": {
                "type": CFNString,
                "description": "A"},
            "b": {
                "type": CFNString,
                "description": "B"}
        }
        params = {"a": None, "c": "Carrot"}
        p = _resolve_parameters(params, self.bp)
        self.assertNotIn("a", p)

    def test_resolve_parameters_booleans(self):
        self.bp.get_parameter_definitions.return_value = {
            "a": {
                "type": CFNString,
                "description": "A"},
            "b": {
                "type": CFNString,
                "description": "B"},
        }
        params = {"a": True, "b": False}
        p = _resolve_parameters(params, self.bp)
        self.assertEquals("true", p["a"])
        self.assertEquals("false", p["b"])
