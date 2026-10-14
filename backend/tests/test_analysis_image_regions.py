import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from backend.routers.analysis import _fit_image_for_vlm
from backend.services.analysis_image_regions import (
    REGIONS_KEY, mask_analysis_image, regions_for_source, valid_regions,
    CROP_RIGHT_KEY, APPLIED_CROP_RIGHT_KEY, crop_right_for_source,
    prepare_analysis_image, valid_crop_right,
)
from backend.services.expert_image_preparation import prepare_expert_frame
from backend.services.surgr1_window_review import _write_review_image
from backend.services.window_reanalysis import fit_image, load_review_frames


REGIONS = [[.9375, 0, 1, .095], [.895, .915, 1, 1]]
CONFIG = {'analysis_overlay_exclusions': {'quad_right': REGIONS}}


class AnalysisImageRegionTests(unittest.TestCase):
    def source(self):
        image = Image.new('RGB', (2880, 2160), (190, 130, 110))
        image.info.update({REGIONS_KEY: REGIONS, 'source_timestamp': 5.64})
        return image

    def assert_masked(self, image):
        arr = np.asarray(image)
        h, w = arr.shape[:2]
        self.assertTrue((arr[int(.04*h), int(.975*w)] == 0).all())
        self.assertTrue((arr[int(.96*h), int(.95*w)] == 0).all())
        self.assertTrue((arr[h//2, w//2] != 0).all())
        self.assertTrue((arr[int(.5*h), int(.99*w)] != 0).all())
        self.assertTrue((arr[int(.85*h), int(.95*w)] != 0).all())

    def test_rules_are_input_layout_scoped_not_video_or_transport_scoped(self):
        for source in ('decklink://0?input_layout=quad_right',
                       'simulator://file/tmp/test.mp4?input_layout=quad_right',
                       '/tmp/another.mp4?input_layout=quad_right'):
            self.assertEqual(regions_for_source(source, CONFIG), [tuple(r) for r in REGIONS])
        self.assertEqual(regions_for_source('/tmp/test.mp4', CONFIG), [])
        self.assertEqual(regions_for_source('decklink://0', CONFIG), [])
        self.assertEqual(regions_for_source('decklink://0?input_layout=quad_right', {}), [])

    def test_actual_configuration_excludes_icons_in_both_input_layouts(self):
        config = json.loads((Path(__file__).resolve().parents[2] / 'config.json').read_text())
        for source in ('decklink://0?mode=1080p50&connection=sdi',
                       'device://0?input_layout=full',
                       'simulator://file/tmp/test.mp4', '/normal/video.mp4',
                       'device://0?input_layout=quad_right'):
            with self.subTest(source=source):
                self.assertEqual(crop_right_for_source(source, config), .11)
                self.assertEqual(regions_for_source(source, config), [])

    def test_invalid_or_out_of_range_regions_cannot_blank_the_image(self):
        invalid = [None, [0, 0, 0, 1], [-1, 0, 1, 1], [0, 0, 2, 1],
                   ['bad', 0, 1, 1], [float('nan'), 0, 1, 1], [0, 0, 1]]
        self.assertEqual(valid_regions(invalid), [])
        self.assertEqual(valid_regions('not a list'), [])

    def test_mask_preserves_original_dimensions_and_timestamp(self):
        source = self.source()
        masked = mask_analysis_image(source)
        self.assert_masked(masked)
        self.assertEqual(masked.size, source.size)
        self.assertEqual(masked.info['source_timestamp'], 5.64)
        self.assertEqual(source.getpixel((2800, 20)), (190, 130, 110))

    def test_unconfigured_images_are_untouched(self):
        source = Image.new('RGB', (100, 80), (190, 130, 110))
        self.assertIs(mask_analysis_image(source), source)
        self.assertTrue(np.all(np.asarray(prepare_expert_frame(source)) == [110, 130, 190]))

    def test_vlm_and_expert_images_exclude_the_same_regions_after_resize(self):
        source = self.source()
        self.assert_masked(_fit_image_for_vlm(source, 640))
        frame = prepare_expert_frame(source)
        self.assertEqual(frame.shape, (960, 1280, 3))
        self.assert_masked(Image.fromarray(frame))
        self.assert_masked(fit_image(source, REGIONS))
        self.assertEqual(source.size, (2880, 2160))

    def test_private_jpeg_decoder_keeps_attached_exclusion_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'frame.jpg'
            self.source().save(path)
            before = path.read_bytes()
            with Image.open(path) as source:
                source.info[REGIONS_KEY] = REGIONS
                self.assert_masked(Image.fromarray(prepare_expert_frame(source)))
                self.assertEqual(source.size, (2880, 2160))
            self.assertEqual(path.read_bytes(), before)

    def test_surgr1_saved_image_excludes_icons_without_modifying_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'review.jpg'
            source = self.source()
            _write_review_image(source, path)
            with Image.open(path) as result:
                self.assert_masked(result)
                self.assertEqual(result.size, (840, 630))
            self.assertEqual(source.getpixel((2800, 20)), (190, 130, 110))

    def test_history_review_uses_identical_source_profile(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            self.source().save(Path(directory) / 'frame.jpg')
            storage = Mock()
            storage.list_frames_in_range.return_value = [{'path': 'frame.jpg', 'timestamp': 5.64}]
            with patch('backend.services.window_reanalysis.get_frame_storage_service', return_value=storage), \
                 patch('backend.services.window_reanalysis.load_config', return_value=CONFIG):
                images, times = load_review_frames({'storage_path': directory,
                    'video_path': 'decklink://0?input_layout=quad_right'}, 5, 10)
            self.assert_masked(images[0])
            self.assertEqual(times, [5.64])


class AnalysisInputCropTests(unittest.TestCase):
    CONFIG = {'analysis_input_crop_right': {'full': .11, 'quad_right': .11}}

    def source(self):
        image = Image.new('RGB', (2880, 2160), (190, 130, 110))
        # Stand-ins for both device icons, safely inside the removed strip.
        image.paste((0, 255, 0), (2700, 0, 2880, 206))
        image.paste((0, 255, 0), (2580, 1976, 2880, 2160))
        image.info.update({CROP_RIGHT_KEY: .11, 'source_timestamp': 17.26})
        return image

    def assert_icons_removed(self, image):
        self.assertTrue(np.all(np.asarray(image) == [190, 130, 110]))

    def test_same_crop_for_capture_simulator_and_file_not_timestamps_or_filenames(self):
        for source in ('decklink://0?input_layout=quad_right',
                       'simulator://file/tmp/test.mp4?input_layout=quad_right',
                       '/tmp/other.mp4?input_layout=quad_right'):
            self.assertEqual(crop_right_for_source(source, self.CONFIG), .11)
        self.assertEqual(crop_right_for_source('decklink://0', self.CONFIG), .11)
        self.assertEqual(crop_right_for_source('/normal.mp4', self.CONFIG), .11)
        self.assertEqual(crop_right_for_source('decklink://0?input_layout=quad_right', {}), 0)
        self.assertEqual(crop_right_for_source('decklink://0', {}), 0)

    def test_full_hd_models_remove_icons_once_without_changing_preview_or_time(self):
        source = Image.new('RGB', (1920, 1080), (190, 130, 110))
        source.paste((0, 255, 0), (1840, 0, 1920, 80))
        source.paste((0, 255, 0), (1840, 996, 1920, 1080))
        original = source.tobytes()
        source.info.update({CROP_RIGHT_KEY: crop_right_for_source(
            'decklink://0?mode=1080p50&connection=sdi', self.CONFIG),
            'source_timestamp': 17.26})
        expert = prepare_expert_frame(source)
        self.assertEqual(expert.shape, (720, 1139, 3))
        self.assert_icons_removed(Image.fromarray(expert[:, :, ::-1]))
        for image in (_fit_image_for_vlm(source, 640), fit_image(source)):
            self.assertEqual(image.size, (569, 360))
            self.assert_icons_removed(image)
            self.assertEqual(image.info['source_timestamp'], 17.26)
            self.assertEqual(prepare_analysis_image(image).size, image.size)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'r1.jpg'
            _write_review_image(source, path)
            with Image.open(path) as result:
                self.assertEqual(result.width, 747)
                self.assertTrue(np.all(np.asarray(result)[:, :, 1] < 160))
        self.assertEqual(source.size, (1920, 1080))
        self.assertEqual(source.tobytes(), original)
        self.assertEqual(source.info['source_timestamp'], 17.26)

    def test_invalid_settings_cannot_remove_most_of_the_field(self):
        for value in (None, 'bad', [], {}, -1, .9, float('nan'), float('inf')):
            self.assertEqual(valid_crop_right(value), 0)
        self.assertEqual(valid_crop_right(.11), .11)
        self.assertEqual(valid_crop_right(0), 0)

    def test_crop_removes_icons_without_modifying_pixels_or_time_on_source(self):
        source = self.source()
        cropped = prepare_analysis_image(source)
        self.assertEqual(cropped.size, (2563, 2160))
        self.assert_icons_removed(cropped)
        self.assertEqual(cropped.info['source_timestamp'], 17.26)
        self.assertEqual(cropped.info[APPLIED_CROP_RIGHT_KEY], .11)
        self.assertNotIn(CROP_RIGHT_KEY, cropped.info)
        self.assertEqual(source.size, (2880, 2160))
        self.assertEqual(source.info[CROP_RIGHT_KEY], .11)
        self.assertEqual(source.getpixel((2800, 20)), (0, 255, 0))

    def test_expert_vlm_and_history_paths_receive_same_roi(self):
        source = self.source()
        expert = prepare_expert_frame(source)
        self.assertEqual(expert.shape, (960, 1139, 3))
        self.assert_icons_removed(Image.fromarray(expert[:, :, ::-1]))
        for result in (_fit_image_for_vlm(source, 640), fit_image(source)):
            self.assertEqual(result.size, (569, 480))
            self.assert_icons_removed(result)
            self.assertEqual(result.info['source_timestamp'], 17.26)

    def test_nested_encoders_do_not_crop_twice_or_mutate_source(self):
        source = self.source()
        result = _fit_image_for_vlm(source, 640)
        for _ in range(3):
            result = _fit_image_for_vlm(fit_image(result), 640)
            self.assertEqual(result.size, (569, 480))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'r1.jpg'
            _write_review_image(result, path)
            with Image.open(path) as decoded:
                self.assertEqual(decoded.size, (569, 480))
        self.assertEqual(source.size, (2880, 2160))

    def test_surgr1_and_private_jpeg_expert_decoder_apply_crop(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.jpg'
            self.source().save(path)
            before = path.read_bytes()
            with Image.open(path) as source:
                source.info.update({CROP_RIGHT_KEY: .11, 'source_timestamp': 17.26})
                frame = prepare_expert_frame(source)
                self.assertEqual(frame.shape, (960, 1139, 3))
                self.assertEqual(source.size, (2880, 2160))
                self.assertTrue(np.all(frame[:, :, 1] < 160))
                review_path = Path(directory) / 'review.jpg'
                _write_review_image(source, review_path)
                with Image.open(review_path) as review:
                    self.assertEqual(review.size, (747, 630))
                    self.assertTrue(np.all(np.asarray(review)[:, :, 1] < 160))
            self.assertEqual(path.read_bytes(), before)

    def test_history_saved_frames_use_crop_once(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            self.source().save(Path(directory) / 'frame.jpg')
            storage = Mock()
            storage.list_frames_in_range.return_value = [{'path': 'frame.jpg', 'timestamp': 17.26}]
            for source in ('decklink://0?input_layout=quad_right',
                           'decklink://0?mode=1080p50&connection=sdi'):
                with self.subTest(source=source), \
                     patch('backend.services.window_reanalysis.get_frame_storage_service', return_value=storage), \
                     patch('backend.services.window_reanalysis.load_config', return_value=self.CONFIG):
                    images, times = load_review_frames({'storage_path': directory,
                        'video_path': source}, 15, 20)
                self.assertEqual(images[0].size, (569, 480))
                self.assertEqual(prepare_analysis_image(images[0]).size, (569, 480))
                self.assertTrue(np.all(np.asarray(images[0])[:, :, 1] < 160))
                self.assertEqual(times, [17.26])

    def test_history_private_file_decoder_applies_layout_then_model_crop(self):
        from unittest.mock import Mock
        from backend.services import window_reanalysis as review
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.mp4'
            path.touch()
            frame = np.full((200, 400, 3), [110, 130, 190], dtype=np.uint8)
            frame[:, :100] = [0, 0, 255]
            frame[:30, 385:] = [0, 255, 0]
            frame[180:, 370:] = [0, 255, 0]
            capture = Mock()
            capture.read.return_value = (True, frame)
            capture.get.side_effect = [1500, 4500, 7500, 10500]
            with patch.object(review.cv2, 'VideoCapture', return_value=capture), \
                 patch.object(review, 'load_config', return_value=self.CONFIG):
                images, times = load_review_frames({'video_path': f'{path}?input_layout=quad_right'}, 0, 12)
            self.assertEqual(times, [1.5, 4.5, 7.5, 10.5])
            for image in images:
                self.assertEqual(image.size, (267, 200))
                self.assert_icons_removed(image)
                self.assertEqual(prepare_analysis_image(image).size, (267, 200))
            capture.release.assert_called_once()
            self.assertEqual(tuple(frame[0, 0]), (0, 0, 255))
            self.assertEqual(tuple(frame[0, -1]), (0, 255, 0))

    def test_legacy_masks_are_applied_before_crop_and_consumed_once(self):
        source = self.source()
        source.info[REGIONS_KEY] = [[.25, 0, .5, .25]]
        result = prepare_analysis_image(source)
        self.assertEqual(result.size, (2563, 2160))
        self.assertEqual(result.getpixel((1000, 100)), (0, 0, 0))
        self.assertNotIn(REGIONS_KEY, result.info)
        self.assertIs(prepare_analysis_image(result), result)
