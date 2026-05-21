import unittest

from terminal_animation import Frame, centered_origin, visible_line_slice


class FrameTests(unittest.TestCase):
    def test_frame_measures_padded_lines(self):
        frame = Frame(('    ', '  '))

        self.assertEqual(frame.width, 4)
        self.assertEqual(frame.height, 2)


class LayoutTests(unittest.TestCase):
    def test_centered_origin_uses_terminal_and_frame_dimensions(self):
        self.assertEqual(centered_origin(100, 40, 64, 24), (18, 8))

    def test_centered_origin_can_crop_oversized_frames(self):
        self.assertEqual(centered_origin(10, 6, 14, 8), (-2, -1))

    def test_visible_line_slice_crops_left_side(self):
        self.assertEqual(visible_line_slice('012345', -2, 4), (0, '2345'))

    def test_visible_line_slice_crops_right_side(self):
        self.assertEqual(visible_line_slice('012345', 2, 5), (2, '012'))


if __name__ == '__main__':
    unittest.main()
