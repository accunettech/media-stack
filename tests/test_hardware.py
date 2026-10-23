import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from hardware import compose_override, decoder_capabilities, discover, encoding_settings, prepare, select_device
from stack_env import ROOT, compose


class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.baseline=json.loads((ROOT/'config/stack-defaults.json').read_text())['jellyfin']['encoding']
        self.intel={'mode':'intel','device':'/dev/dri/renderD129','group':'991','label':'Intel test'}
        self.amd={'mode':'amd','device':'/dev/dri/renderD128','group':'44','label':'AMD test'}
        self.nvidia={'mode':'nvidia','device':'GPU-test-uuid','label':'NVIDIA test'}

    def test_no_gpu_uses_software_without_device_mapping(self):
        selected=select_device([])
        self.assertEqual(selected['mode'],'software')
        self.assertEqual(compose_override(selected),{'services':{'jellyfin':{}}})
        encoding=encoding_settings(self.baseline,selected,{})
        self.assertEqual(encoding['HardwareAccelerationType'],'none')
        self.assertFalse(encoding['EnableHardwareEncoding'])
        self.assertFalse(encoding['EnableTonemapping'])
        self.assertEqual(encoding['HardwareDecodingCodecs'],[])

    def test_explicit_missing_gpu_is_error_not_silent_fallback(self):
        with self.assertRaises(ValueError):select_device([],mode='intel')
        with self.assertRaises(ValueError):select_device([self.intel],device='/dev/dri/renderD128')

    def test_intel_preferred_and_multi_gpu_override(self):
        self.assertEqual(select_device([self.amd,self.intel])['mode'],'intel')
        self.assertEqual(select_device([self.amd,self.intel],mode='amd')['mode'],'amd')
        self.assertEqual(select_device([self.intel],device='/dev/dri/renderD129')['device'],'/dev/dri/renderD129')

    def test_interactive_choice_can_select_software(self):
        with patch('builtins.input',return_value='2'),patch('builtins.print'):
            self.assertEqual(select_device([self.intel],interactive=True)['mode'],'software')

    def test_decode_profiles_do_not_enable_unsupported_codecs(self):
        text='VAProfileH264High : VAEntrypointVLD\nVAProfileHEVCMain10 : VAEntrypointVLD\nVAProfileAV1Profile0 : VAEntrypointEncSlice\n'
        caps=decoder_capabilities(text)
        self.assertEqual(caps['decoders'],['h264']);self.assertTrue(caps['hevc10']);self.assertFalse(caps['vp910'])
        self.assertNotIn('av1',caps['decoders']);self.assertNotIn('vc1',caps['decoders'])

    def test_amds_driver_and_permissions_are_local(self):
        jelly=compose_override(self.amd)['services']['jellyfin']
        self.assertEqual(jelly['environment']['LIBVA_DRIVER_NAME'],'radeonsi')
        self.assertEqual(jelly['group_add'],['44'])
        self.assertEqual(jelly['devices'],['/dev/dri/renderD128:/dev/dri/renderD128'])

    def test_nvidia_uses_toolkit_reservation_without_intel_device(self):
        jelly=compose_override(self.nvidia)['services']['jellyfin']
        self.assertNotIn('devices',jelly);self.assertNotIn('LIBVA_DRIVER_NAME',jelly['environment'])
        request=jelly['deploy']['resources']['reservations']['devices'][0]
        self.assertEqual(request['device_ids'],['GPU-test-uuid']);self.assertEqual(request['driver'],'nvidia')

    def test_unverified_low_power_and_tonemapping_stay_off(self):
        e=encoding_settings(self.baseline,self.intel,{'decoders':['h264'],'hevc_encode':True})
        self.assertEqual(e['QsvDevice'],'/dev/dri/renderD129')
        self.assertFalse(e['EnableIntelLowPowerH264HwEncoder'])
        self.assertFalse(e['EnableTonemapping']);self.assertFalse(e['AllowAv1Encoding'])
        self.assertTrue(e['AllowHevcEncoding'])

    def test_failed_probe_restores_previous_compose_file(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'compose.json';p.write_text('original')
            env={'STACK_HARDWARE_DIR':td}
            with patch('hardware.discover',return_value=[self.intel]),patch('hardware.compose'),patch('hardware.Probe.capabilities',side_effect=ValueError('driver')):
                with self.assertRaises(ValueError):prepare(env)
            self.assertEqual(p.read_text(),'original');self.assertFalse((Path(td)/'profile.json').exists())

    def test_compose_wrapper_loads_generated_profile(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td)/'compose.json').write_text('{}')
            env={'STACK_HARDWARE_DIR':td}
            with patch('stack_env.subprocess.run') as run:compose(['config','--quiet'],env)
            self.assertIn(str(Path(td)/'compose.json'),run.call_args.args[0])
            with patch('stack_env.subprocess.run') as run:compose(['ps'],{**env,'STACK_COMPOSE_FILE':'/tmp/custom.json'})
            self.assertNotIn(str(Path(td)/'compose.json'),run.call_args.args[0])

    def test_discover_reads_vendor_and_selected_node(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);d=root/'sys/renderD129/device';d.mkdir(parents=True)
            (d/'vendor').write_text('0x1002\n');(d/'device').write_text('0x1234\n')
            (root/'dev').mkdir();(root/'dev/renderD129').touch()
            with patch('hardware.subprocess.run',side_effect=FileNotFoundError):devices=discover(root/'sys',root/'dev')
            self.assertEqual(len(devices),1);self.assertEqual(devices[0]['mode'],'amd')


if __name__=='__main__':unittest.main()
