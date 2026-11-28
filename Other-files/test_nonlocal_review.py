"""PPMS-relevant Zurich MFLI demodulator routing and MF-MD tests (preserved from Non-local review)."""

import unittest
from unittest import mock

from transport.ppms.controllers.zurich import NONLOCAL_SAMPLE_HZ, ZurichMFLIController


class ZurichControllerTests(unittest.TestCase):
    def controller(self):
        return ZurichMFLIController(lambda _m: None)

    def test_unused_demodulators_follow_the_reference_routing(self):
        controller = self.controller()
        controller.mfli_bridge = mock.Mock()
        controller.mfli_config = {"extref_demodulator": 3}
        for mode, indices in (("1", [1, 2]), ("1+2", [2]), ("1+2+3", None)):
            controller.mfli_bridge.reset_mock()
            controller.disable_unused_ppms_demods(mode)
            if indices is None:
                controller.mfli_bridge.request.assert_not_called()
            else:
                controller.mfli_bridge.request.assert_called_once_with(
                    "disable_unused_demods", indices=indices
                )
        controller.mfli_props = {"options": "MD"}
        controller.mfli_config = {"extref_demodulator": 1}
        controller.mfli_bridge.reset_mock()
        controller.disable_unused_ppms_demods("1+2")
        controller.mfli_bridge.request.assert_called_once_with(
            "disable_unused_demods", indices=[3]
        )
        controller.mfli_props = {
            "options": "",
            "has_demod_2": True,
            "has_demod_4": False,
        }
        for extref in (0, 1):
            controller.mfli_config = {"extref_demodulator": extref}
            controller.mfli_bridge.reset_mock()
            controller.disable_unused_ppms_demods("1")
            controller.mfli_bridge.request.assert_not_called()

    def test_mf_md_is_the_option_or_a_fourth_demodulator_not_the_second(self):
        controller = self.controller()
        controller.mfli_bridge = mock.Mock()
        for props, multi in (
            ({"options": "MD"}, True),
            ({"options": "IA"}, False),
            ({"options": "", "has_demod_2": True, "has_demod_4": False}, False),
            ({"options": "", "has_demod_2": True, "has_demod_4": True}, True),
            ({"options": "", "has_demod_2": True}, False),
        ):
            with self.subTest(props=props):
                controller.mfli_props = dict(props)
                self.assertIs(controller.mfli_has_multi_demod(), multi)
        controller.mfli_props = {
            "options": "",
            "has_demod_2": True,
            "has_demod_4": False,
        }
        demod = {
            "x": 1e-6,
            "y": 0.0,
            "frequency": 317.3,
            "timestamp": 1,
            "harmonic": 1,
            "adcselect": 0,
            "enabled": 1,
        }
        reference = dict(demod, adcselect=8)
        controller.mfli_bridge.request.return_value = {"demods": [demod, reference]}
        with mock.patch.object(
            type(controller),
            "mfli_connected",
            new_callable=mock.PropertyMock,
            return_value=True,
        ):
            fields = controller.read_mfli_four_demods()
        controller.mfli_bridge.request.assert_called_once_with("read_demods", count=2)
        self.assertEqual(
            (fields["zurich_demod1_x_v"], fields["zurich_demod1_status"]),
            (1e-6, "enabled"),
        )
        self.assertEqual(
            (fields["zurich_demod2_input"], fields["zurich_demod2_status"]),
            (8, "enabled"),
        )
        self.assertEqual(
            [fields[f"zurich_demod{i}_status"] for i in (3, 4)], ["disabled"] * 2
        )
        with self.assertRaisesRegex(
            RuntimeError, "Demod 4 external reference requires MF-MD"
        ):
            controller.configure_mfli(
                "AC",
                2,
                "R",
                reference_source="Aux Input 1",
                sample_rate_hz=NONLOCAL_SAMPLE_HZ,
                input_impedance="10 MΩ",
                input_scaling=1.0,
                timeconstant_s=1.0,
                external_reference_demodulator=3,
            )
        controller.mfli_bridge.request.reset_mock()
        controller.mfli_bridge.request.side_effect = RuntimeError(
            "stop after the request"
        )
        with self.assertRaisesRegex(RuntimeError, "stop after the request"):
            controller.configure_mfli(
                "AC",
                2,
                "R",
                reference_source="Aux Input 1",
                sample_rate_hz=NONLOCAL_SAMPLE_HZ,
                input_impedance="10 MΩ",
                input_scaling=1.0,
                timeconstant_s=1.0,
            )
        self.assertEqual(
            controller.mfli_bridge.request.call_args.kwargs["extref_demod"], 1
        )
        self.assertEqual(
            controller.mfli_bridge.request.call_args.kwargs["harmonic"], 2
        )


if __name__ == "__main__":
    unittest.main()
