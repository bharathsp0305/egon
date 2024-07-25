import os, sys
import tempfile
import time, datetime
import logging
import argparse, shlex
import subprocess
import textwrap

from utils.tc import Timecode
import utils.pyseq as pyseq
from utils.connection import *


try:
    import oiio
    import numpy as np
    from PIL import Image, ImageEnhance
except ImportError:
    print(
        "Error: Missing dependencies. Need:\n\tOpenImageIO\n\tNumPy\n\tPyYAML\n\tPillow (for mjpeg codec conversion)"
    )


dir_path = os.path.dirname(os.path.realpath(__file__))

DEFAULT_CODEC = "hevc"
DEFAULT_OCIO_FILE_PATH = os.path.join(dir_path, "configs", "config.ocio")
DEFAULT_OCIO_TRANSFORM = ["linear", "sRGB"]

log = logging.getLogger(__name__)


class GenrateDaily:
    def __init__(
        self,
        input_path=None,
        config=None,
        output=None,
        project=None,
        task_id=None,
        scope=None,
        **kwargs,
    ):
        self.start_time = time.time()
        self.setup_success = False
        self.output_codecs_file = None
        self.renamed_file = ""
        self.first_frame_path = ""
        self.framecounter = 0

        self.input_path = input_path
        self.config = config
        self.output = output
        self.project = project
        self.task_id = task_id
        self.scope = scope

        for key, value in kwargs:
            setattr(self, key, value)

        parser = argparse.ArgumentParser(
            description="Process given image sequence with ocio display, resize and output to ffmpeg for encoding into a dailies movie."
        )

        parser.add_argument(
            "input_path",
            help="Input exr image sequence. Can be a folder containing images, a path to the first image, a percent 05d path, or a ##### path.",
        )

        parser.add_argument("-s", "--scope", help="Scope Id : Thadam Scope ID.")

        # Show help if no args.
        if len(sys.argv) == 1:
            parser.print_help()
            return None

        args = parser.parse_args()

        self.connection = Connection(username="PFXHO_048", password="Bh@r@th123")

        # Parse Config File
        if not self.config:
            try:
                self.config = self.connection.get_slate_configuration(
                    proj_code="bn2", daily_type="INTERNAL"
                )
            except Exception as e:
                log.error(f"Error : {e}")
                self.setup_success = False
                return

        if not self.output_codecs_file:
            self.output_codecs_file = self.connection.get_attribute_codec(getcodec=True)
        else:
            print("Error: Could not get Codec")
            self.setup_success = False
            return

        self.datalist = self.connection.get_datalist(
            scope_name="Asset/env/CHAD_LAB_BUNKER_EXTERIOR",
            proj_code="bn2",
            task_id="125880",
        )

        self.slate_profile = self.config.get("slate_profiles")

        self.globals_config = self.config.get("globals")
        input_path = args.input_path
        codec = self.globals_config.get("output_codec")
        self.movie_location = None

        if not codec:
            codec = DEFAULT_CODEC

        self.codec_config = self.output_codecs_file.get(codec)

        self.image_sequences = self.get_image_sequences(input_path)

        if not self.image_sequences:
            print("No image sequence found! Exiting...")
            self.setup_success = False
            return

        self.ocioconfig = self.globals_config.get("ocioconfig")
        print("OCIO Config: {0}".format(self.ocioconfig))
        if self.ocioconfig:
            log.debug("Got OCIO config from config: {0}".format(self.ocioconfig))
        # Try to get ocio config from $OCIO env-var if it's not defined
        if not self.ocioconfig:
            env_ocio = os.getenv("OCIO")
            if env_ocio:
                self.ocioconfig = env_ocio
            else:
                self.ocioconfig = DEFAULT_OCIO_FILE_PATH

        if not os.path.exists(self.ocioconfig):
            log.warning(
                "OCIO Config does not exist: \n\t{0}\n\tNo OCIO color transform will be applied".format(
                    self.ocioconfig
                )
            )
            self.ocioconfig = None

        # Get default ocio transform to use if none is passed by commandline
        self.ociocolorconvert = self.globals_config.get("ocio_transform")

        if self.ociocolorconvert:
            log.debug(
                "Got OCIO Transform from config: {0}".format(self.ociocolorconvert)
            )
        else:
            # No ocio color transform specified
            print("Warning: No default ocio transform specified, Using default OCIO.")
            self.ociocolorconvert = DEFAULT_OCIO_TRANSFORM

        self.output_width = self.globals_config["width"]
        self.output_height = self.globals_config["height"]

        if not self.output_width or not self.output_height:
            buf = oiio.ImageBuf(self.image_sequence[0].path)
            spec = buf.spec()
            iar = float(spec.width) / float(spec.height)
            if not self.output_width:
                self.output_width = spec.width
                self.globals_config["width"] = self.output_width
            if not self.output_height:
                self.output_height = int(round(self.output_width / iar))
                self.globals_config["height"] = self.output_height
            buf.close()

        self.setup_success = True

        if self.setup_success == True:
            for self.image_sequence in self.image_sequences:
                self.process()

    def process(self):
        """
        Performs the actual processing of the movie.
        Args:
            None
        Returns:
            None
        """

        # Set up movie file location and naming

        # Crop separating character from sequence basename if there is one.
        seq_basename = self.image_sequence.head()

        if seq_basename.endswith(self.image_sequence.parts[-2]):
            seq_basename = seq_basename[:-1]

        movie_ext = self.globals_config["movie_ext"]
        slate_type = self.globals_config["slate_type"]

        # Create full movie filename

        current_datetime = datetime.datetime.now()
        datetime_str = current_datetime.strftime("%d_%m_%Y_%H_%M")
        movie_basename = seq_basename + "_" + datetime_str + "_" + slate_type
        movie_filename = movie_basename + "." + movie_ext

        # Handle relative / absolute paths for movie location
        # use globals config for movie location if none specified on the commandline
        if not self.movie_location:
            self.movie_location = self.globals_config["movie_location"]
            print(
                "No output folder specified. Using Output folder from globals: {0}".format(
                    self.movie_location
                )
            )

        if self.movie_location.startswith("/"):
            # Absolute path specified
            self.movie_fullpath = os.path.join(self.movie_location, movie_filename)
        elif self.movie_location.startswith("~"):
            # Path referencing home folder specified
            self.movie_location = os.path.expanduser(self.movie_location)
            self.movie_fullpath = os.path.join(self.movie_location, movie_filename)
        elif self.movie_location.startswith(".") or self.movie_location.startswith(
            ".."
        ):
            # Relative path specified - will output relative to image sequence directory
            self.movie_fullpath = os.path.join(
                self.image_sequence.dirname, self.movie_location, movie_filename
            )
        else:
            self.movie_fullpath = os.path.join(self.movie_location, movie_filename)

        # Check output dir exists
        if not os.path.exists(os.path.dirname(self.movie_fullpath)):
            try:
                os.makedirs(os.path.dirname(self.movie_fullpath))
            except OSError:
                print(
                    "Output directory does not exist and do not have permission to create it: \n\t{0}".format(
                        os.path.dirname(self.movie_fullpath)
                    )
                )
                return

        # Set up Logger
        log_fullpath = os.path.splitext(self.movie_fullpath)[0] + ".log"
        if os.path.exists(log_fullpath):
            os.remove(log_fullpath)
        handler = logging.FileHandler(log_fullpath)
        handler.setFormatter(
            logging.Formatter(
                "%(levelname)s\t %(asctime)s \t%(message)s", "%Y-%m-%dT%H:%M:%S"
            )
        )
        log.addHandler(handler)
        if self.globals_config["debug"]:
            log.setLevel(logging.DEBUG)
        else:
            log.setLevel(logging.INFO)
        log.debug(
            "Got config:\n\tCodec Config:\t{0}\n\tImage Sequence Path:\n\t\t{1}".format(
                self.codec_config["name"], self.image_sequence.path()
            )
        )

        log.debug(
            "Output width x height: {0}x{1}".format(
                self.output_width, self.output_height
            )
        )

        # Set pixel_data_type based on config bitdepth
        if self.codec_config["bitdepth"] > 8:
            self.pixel_data_type = oiio.UINT16
        else:
            self.pixel_data_type = oiio.UINT8

        # Get timecode based on frame
        tc = Timecode(self.globals_config["framerate"], start_timecode="00:00:00:00")
        self.start_tc = tc + self.image_sequence.start()

        ffmpeg_args = self.setup_ffmpeg()

        log.info("ffmpeg command:\n\t{0}".format(ffmpeg_args))

        # Static image buffer for text that doesn't change frame to frame
        self.static_text_buf_zero_frame = oiio.ImageBuf(
            oiio.ImageSpec(
                self.output_width, self.output_height, 4, self.pixel_data_type
            )
        )

        self.static_text_buf_first_frame = oiio.ImageBuf(
            oiio.ImageSpec(
                self.output_width, self.output_height, 4, self.pixel_data_type
            )
        )

        self.zero_frame = self.slate_profile.get("zero_frame")

        # Loop through each text element, create the text image, and add it to self.static_text_buf_zero_frame
        zero_frame_text_elements = self.zero_frame.get("static_text_elements")
        if zero_frame_text_elements:

            images = self.zero_frame.get("images")
            for image_name, image_prop in images.items():
                self.static_text_buf_zero_frame = self.create_image(
                    image_prop, self.static_text_buf_zero_frame
                )

            for text_element_name, text_element in zero_frame_text_elements.items():
                log.info("Generate Text")
                self.generate_text(
                    text_element_name, text_element, self.static_text_buf_zero_frame
                )

        self.first_frame = self.slate_profile.get("first_frame")

        first_frame_text_elements = self.first_frame.get("static_text_elements")
        if first_frame_text_elements:
            for text_element_name, text_element in first_frame_text_elements.items():
                log.info("Generate Text")
                self.generate_text(
                    text_element_name, text_element, self.static_text_buf_first_frame
                )

            # Invoke ffmpeg subprocess
        ffproc = subprocess.Popen(
            shlex.split(ffmpeg_args), stdin=subprocess.PIPE, stdout=subprocess.PIPE
        )

        # Loop through every frame, passing the result to the ffmpeg subprocess

        for i, self.frame in enumerate(self.image_sequence, 1):

            log.info(
                "Processing frame {0:04d}: \t{1:04d} of {2:04d}".format(
                    self.frame.frame, i, self.image_sequence.length()
                )
            )
            # elapsed_time = datetime.timedelta(seconds = time.time() - start_time)
            # log.info("Time Elapsed: \t{0}".format(elapsed_time))
            frame_start_time = time.time()

            if i == 1:
                buf = self.process_frame(self.frame, zero_frame=True)

            else:
                buf = self.process_frame(self.frame)

                images = self.first_frame.get("images")
                for image_name, image_prop in images.items():
                    buf = self.create_image(image_prop, buf)

                first_frame_dynamic_text_elements = self.first_frame.get(
                    "dynamic_text_elements"
                )
                if first_frame_dynamic_text_elements:
                    for (
                        text_element_name,
                        text_element,
                    ) in first_frame_dynamic_text_elements.items():
                        log.info("Generate Text")
                        self.generate_text(text_element_name, text_element, buf)

            pixels = buf.get_pixels(self.pixel_data_type)
            if self.codec_config["name"] == "mjpeg":
                jpeg_img = Image.fromarray(pixels, mode="RGB")
                jpeg_img.save(ffproc.stdin, "JPEG", subsampling="4:4:4", quality=95)
            else:
                ffproc.stdin.write(pixels)

            frame_elapsed_time = datetime.timedelta(
                seconds=time.time() - frame_start_time
            )
            log.info("Frame Processing Time: \t{0}".format(frame_elapsed_time))

        elapsed_time = datetime.timedelta(seconds=time.time() - self.start_time)
        log.info("Total Processing Time: \t{0}".format(elapsed_time))
        if self.renamed_file != "":
            os.remove(self.renamed_file)

    def create_image(self, image, buf):
        try:
            temp_file_path = None
            opacity = image.get("opacity")

            with tempfile.NamedTemporaryFile(delete=False, suffix=f".jpg") as temp_file:
                temp_file_path = temp_file.name
                buf.write(temp_file_path)
                pillow_buf_image = Image.open(temp_file_path).convert("RGBA")
                pillow_fg_image = Image.open(image.get("src")).convert("RGBA")
                bg_width, bg_height = pillow_buf_image.size
                width, height = pillow_fg_image.size
                scale_factor = image.get("scale")
                x_offset = image.get("offset")[0]
                y_offset = image.get("offset")[1]

                new_width = int(width * scale_factor)
                new_height = int(height * scale_factor)

                x_offset = int((bg_width - new_width) * x_offset)
                y_offset = int((bg_height - new_height) * y_offset)

                pillow_fg_image = pillow_fg_image.resize((new_width, new_height))
                if opacity != 1.0:
                    alpha = pillow_fg_image.split()[3]
                    alpha = ImageEnhance.Brightness(alpha).enhance(opacity)
                    pillow_fg_image.putalpha(alpha)

                pillow_buf_image.alpha_composite(pillow_fg_image, (x_offset, y_offset))
                composited_image_np = np.array(pillow_buf_image)
                composited_image_buf = oiio.ImageBuf(composited_image_np)
                buf.copy_pixels(composited_image_buf)
        except Exception as e:

            log.error("Error Placing Image : {0}".format(e))
        finally:
            if temp_file_path:
                os.remove(temp_file_path)

        return buf

    def generate_text(self, text_element_name, text_element, buf):
        """
        Generate text and write it into an image buffer.

        Args:
            text_element_name: the name of the text element to search for in the config
            text_element: the config dict to use
            buf: the oiio.ImageBuf object to write the pixels into

        Returns:
            Returns the modified oiio.ImageBuf object with text added.
        """

        # Text Elements
        log.debug("Processing text element: {0}".format(text_element_name))

        # Inherit globals if an element in text_element is not defined
        font = text_element["font"]
        if not os.path.isfile(font):
            log.error("Specified font does not exist! Using default font.")
            font_path = os.path.join(dir_path, "fonts", "Roboto", "Roboto-Regular.ttf")
            font = font_path

        # Calculate font size and position
        font_size = text_element["font_size"]
        font_color = text_element["font_color"]
        box = text_element["box"]
        justify = None
        if "justify" not in text_element:
            justify = "left"
        else:
            justify = text_element["justify"]

        if justify != "left" or justify != "center":
            justify = "left"

        # Scale back to pixels from %
        box_ll = [int(box[0] * self.output_width), int(box[1] * self.output_height)]
        box_ur = [int(box[2] * self.output_width), int(box[3] * self.output_height)]
        font_size = int(font_size * self.output_width)

        # Get text to display
        if text_element_name == "framecounter":
            if self.frame.frame == 0:
                text_contents = ""
            else:
                text_contents = str(self.frame.frame).zfill(4)
        else:
            if text_element["islabel"]:
                text_contents = text_element["value"]
            else:
                text_contents = self.datalist[f"{text_element_name}"]

        text_contents = textwrap.fill(text_contents, width=40)
        # Convert from Nuke-style (reference = lower left) to OIIO Style (reference = upper left)
        box_ll[1] = int(self.output_height - box_ll[1])
        box_ur[1] = int(self.output_height - box_ur[1])

        # Calculate the width of the text
        text_roi = oiio.ImageBufAlgo.text_size(
            text_contents, fontsize=font_size, fontname=font
        )

        # Calculate the new upper right x-coordinate based on the text width
        box_ur[0] = box_ll[0] + text_roi.width

        # Adjust the box width to fit the text
        box_width = box_ur[0] - box_ll[0]

        # Update the box_ur coordinate
        box_ur[0] = box_ll[0] + box_width

        # Add text height to position
        box_ll[1] = int(box_ll[1] + text_roi.height)
        box_width = box_ur[0] - box_ll[0]

        if text_contents:
            log.debug(
                "Text Output: \n\t\t\t\t{0}, {1}, {2}, {fontsize}, {textcolor}, {shadow}".format(
                    box_ll[0],
                    box_ll[1],
                    text_contents,
                    fontsize=font_size,
                    fontname=font,
                    textcolor=(
                        font_color[0],
                        font_color[1],
                        font_color[2],
                        font_color[3],
                    ),
                    shadow=0,
                )
            )

            oiio.ImageBufAlgo.render_text(
                buf,
                box_ll[0],
                box_ll[1],
                text_contents,
                fontsize=font_size,
                fontname=font,
                textcolor=(
                    font_color[0],
                    font_color[1],
                    font_color[2],
                    font_color[3],
                ),
                alignx=justify,
                aligny="bottom",
                shadow=0,
                roi=oiio.ROI.All,
                nthreads=0,
            )
        else:
            log.warning(
                "Warning: No text specified for text element {0}".format(
                    text_element_name
                )
            )
        return buf

    def process_frame(self, frame, zero_frame=False):
        """
        Apply all color and reformat / resize operations to input image, then return the imagebuf

        Args:
            frame: pyseq Item object describing the current frame.
            framenumber: the current frame number

        Returns:
            Returns an oiio.ImageBuf object which holds the altered image data.
        """

        # Setup image buffer
        buf = oiio.ImageBuf(frame.path)
        spec = buf.spec()

        # Get Codec Config and gather information
        iwidth = spec.width
        iheight = spec.height
        if float(iheight) != 0:
            iar = float(iwidth) / float(iheight)
        else:
            log.error("Input height is Zero! Skipping frame {0}".format(frame))
            return

        px_filter = self.globals_config.get("filter")
        self.output_width = self.globals_config.get("width")
        self.output_height = self.globals_config.get("height")
        fit = self.globals_config.get("fit")
        cropwidth = self.globals_config.get("cropwidth")
        cropheight = self.globals_config.get("cropheight")

        # Remove alpha channel
        oiio.ImageBufAlgo.channels(buf, buf, (0, 1, 2))

        # Apply ocio color transform
        buf = self.apply_ocio_transform(buf)

        # Setup for width and height
        if not self.output_width:
            resize = False
        else:
            resize = True
            # If no output height specified, resize keeping aspect ratio, long side = width - calc height
            oheight_noar = int(self.output_width / iar)
            if not self.output_height:
                self.output_height = oheight_noar
            oar = float(self.output_width) / float(self.output_height)

        # Apply cropwidth / cropheight to remove pixels on edges before applying resize
        if cropwidth or cropheight:
            # Handle percentages
            if type(cropwidth) == str:
                if "%" in cropwidth:
                    cropwidth = int(float(cropwidth.split("%")[0]) / 100 * iwidth)
                    log.info("Got crop width percentage: {0}px".format(cropwidth))
            if type(cropheight) == str:
                if "%" in cropheight:
                    cropheight = int(float(cropheight.split("%")[0]) / 100 * iheight)
                    log.info("Got crop height percentage: {0}px".format(cropheight))

            log.debug(
                "Not Yet CROPPED:{0} {1}".format(buf.spec().width, buf.spec().height)
            )

            buf = oiio.ImageBufAlgo.crop(
                buf,
                roi=oiio.ROI(
                    int(cropwidth / 2),
                    int(iwidth - cropwidth / 2),
                    int(cropheight / 2),
                    int(iheight - cropheight / 2),
                ),
            )

            # Remove data window of buffer so resize works from cropped region
            buf.set_full(
                buf.roi.xbegin,
                buf.roi.xend,
                buf.roi.ybegin,
                buf.roi.yend,
                buf.roi.chbegin,
                buf.roi.chend,
            )

            log.debug("CROPPED:{0} {1}".format(buf.spec().width, buf.spec().height))

            # Recalculate input resolution and aspect ratio - since it may have changed with crop
            iwidth = buf.spec().width
            iheight = buf.spec().height
            iar = float(iwidth) / float(iheight)
            oheight_noar = int(self.output_width / iar)

            log.debug(
                "iwidth:{0} x iheight:{1} x iar: {2}".format(iwidth, iheight, iar)
            )

        # Apply Resize / Fit
        # If input and output resolution are the same, do nothing
        # If output width is bigger or smaller than input width, first resize without changing input aspect ratio
        # If "fit" is true,
        # If output height is different than input height: transform by the output height - input height / 2 to center,
        # then crop to change the roi to the output res (crop moves upper left corner)

        identical = self.output_width == iwidth and self.output_height == iheight
        resize = not identical and resize

        if resize:
            log.info(
                "Performing Resize: \n\t\t\tinput: {0}x{1} ar{2}\n\t\t\toutput: {3}x{4} ar{5}".format(
                    iwidth, iheight, iar, self.output_width, self.output_height, oar
                )
            )

            if iwidth != self.output_width:
                # Perform resize, no change in AR
                log.debug(
                    "iwidth does not equal output_width: oheight noar: {0}, pxfilter: {1}".format(
                        oheight_noar, px_filter
                    )
                )

                #############
                #
                if px_filter:
                    # (bug): using "lanczos3", 6.0, and upscaling causes artifacts
                    # (bug): dst buf must be assigned or ImageBufAlgo.resize doesn't work
                    buf = oiio.ImageBufAlgo.resize(
                        buf,
                        px_filter,
                        roi=oiio.ROI(0, self.output_width, 0, oheight_noar),
                    )
                else:
                    buf = oiio.ImageBufAlgo.resize(
                        buf, roi=oiio.ROI(0, self.output_width, 0, oheight_noar)
                    )

            if fit:
                # If fitting is enabled..
                height_diff = self.output_height - oheight_noar
                log.debug(
                    "Height difference: {0} {1} {2}".format(
                        height_diff, self.output_height, oheight_noar
                    )
                )

                # If we are cropping to a smaller height we need to transform first then crop
                # If we pad to a taller height, we need to crop first, then transform.
                if self.output_height < oheight_noar:
                    # If we are cropping...
                    buf = self.oiio_transform(buf, 0, height_diff / 2)
                    buf = oiio.ImageBufAlgo.crop(
                        buf, roi=oiio.ROI(0, self.output_width, 0, self.output_height)
                    )
                elif self.output_height > oheight_noar:
                    # If we are padding...
                    buf = oiio.ImageBufAlgo.crop(
                        buf, roi=oiio.ROI(0, self.output_width, 0, self.output_height)
                    )
                    buf = self.oiio_transform(buf, 0, height_diff / 2)

        oiio.ImageBufAlgo.channels(buf, buf, (0, 1, 2, 1.0))
        if zero_frame:
            buf = oiio.ImageBufAlgo.over(self.static_text_buf_zero_frame, buf)
        else:
            buf = oiio.ImageBufAlgo.over(self.static_text_buf_first_frame, buf)
        oiio.ImageBufAlgo.channels(buf, buf, (0, 1, 2))
        return buf

    def oiio_transform(self, buf, xoffset, yoffset):
        """
        Convenience function to reposition an image.

        Args:
            buf: oiio.ImageBuf object representing the image to be transformed.
            xoffset: X offset in pixels
            yoffset: Y offset in pixels

        Returns:
            Returns the modified oiio.ImageBuf object which holds the altered image data.
        """
        orig_roi = buf.roi
        buf.specmod().x += int(xoffset)
        buf.specmod().y += int(yoffset)
        buf_trans = oiio.ImageBuf()
        oiio.ImageBufAlgo.crop(buf_trans, buf, orig_roi)
        return buf_trans

    def apply_ocio_transform(self, buf):
        """
        Applies an ocio transform specified in the config. Can be a ociodisplay, colorconvert, or look transform
        For now only colorconvert is supported.
        Reads from self.ocioconfig to specify the ocio config to use.
        Reads from self.ociocolorconvert, a two item list. [0] is src, [1] is dst colorspace.

        Args:
            buf: oiio.ImageBuf object representing the image to be transformed.

        Returns:
            Returns the modified oiio.ImageBuf object which holds the altered image data.
        """

        if self.ociocolorconvert:
            log.debug(
                "Applying OCIO Config: \n\t{0}\n\t{1} -> {2}".format(
                    self.ocioconfig, self.ociocolorconvert[0], self.ociocolorconvert[1]
                )
            )
            success = oiio.ImageBufAlgo.colorconvert(
                buf,
                buf,
                self.ociocolorconvert[0],
                self.ociocolorconvert[1],
                colorconfig=self.ocioconfig,
                unpremult=False,
            )
            if not success:
                log.error(
                    "Error: OCIO Color Convert failed. Please check that you have the specified colorspaces in your OCIO config."
                )

        return buf

    def setup_ffmpeg(self):
        """
        Constructs an ffmpeg command based on the given codec config.

        Returns:
            A string containing the entire ffmpeg command to run.
        """

        # ffmpeg-10bit No longer necessary in ffmpeg > 4.1
        ffmpeg_command = "ffmpeg"

        if self.codec_config["bitdepth"] >= 10:
            pixel_format = "rgb48le"
        else:
            pixel_format = "rgb24"

        if self.codec_config["name"] == "mjpeg":
            # Set up input arguments for frame input through pipe:
            args = "{0} -y -framerate {1} -i pipe:0".format(
                ffmpeg_command, self.globals_config["framerate"]
            )
        else:
            # Set up input arguments for raw video and pipe:
            args = "{0} -hide_banner -loglevel info -y -f rawvideo -pixel_format {1} -video_size {2}x{3} -framerate {4} -i pipe:0".format(
                ffmpeg_command,
                pixel_format,
                self.globals_config["width"],
                self.globals_config["height"],
                self.globals_config["framerate"],
            )

        # Add timecode so that start frame will display correctly in RV etc
        args += " -timecode {0}".format(self.start_tc)

        if self.codec_config["codec"]:
            args += " -c:v {0}".format(self.codec_config["codec"])

        if self.codec_config["profile"]:
            args += " -profile:v {0}".format(self.codec_config["profile"])

        if self.codec_config["qscale"]:
            args += " -qscale:v {0}".format(self.codec_config["qscale"])

        if self.codec_config["preset"]:
            args += " -preset {0}".format(self.codec_config["preset"])

        if self.codec_config["keyint"]:
            args += " -g {0}".format(self.codec_config["keyint"])

        if self.codec_config["bframes"]:
            args += " -bf {0}".format(self.codec_config["bframes"])

        if self.codec_config["tune"]:
            args += " -tune {0}".format(self.codec_config["tune"])

        if self.codec_config["crf"]:
            args += " -crf {0}".format(self.codec_config["crf"])

        if self.codec_config["pix_fmt"]:
            args += " -pix_fmt {0}".format(self.codec_config["pix_fmt"])

        if self.globals_config["framerate"]:
            args += " -r {0}".format(self.globals_config["framerate"])

        if self.codec_config["vf"]:
            args += " -vf {0}".format(self.codec_config["vf"])

        if self.codec_config["vendor"]:
            args += " -vendor {0}".format(self.codec_config["vendor"])

        if self.codec_config["metadata_s"]:
            args += " -metadata:s {0}".format(self.codec_config["metadata_s"])

        if self.codec_config["bitrate"]:
            args += " -b:v {0}".format(self.codec_config["bitrate"])

        # Finally add the output movie file path
        args += " {0}".format(self.movie_fullpath.replace("\\", "/"))

        return args

    def get_image_sequences(self, input_path):
        """
        Get list of image sequence objects given a path on disk.

        Args:
            input_path: Input file path. Can be a directory or file or %05d / ### style

        Returns:
            An image sequence object.
        """
        input_path = os.path.realpath(input_path)
        input_image_formats = [
            "exr",
            "tif",
            "tiff",
            "png",
            "jpg",
            "jpeg",
            "iff",
            "tex",
            "tx",
            "jp2",
            "j2c",
        ]
        print("Processing INPUT PATH: {0}".format(input_path))
        if os.path.isdir(input_path):
            # Find image sequences recursively inside specified directory
            self.create_temp_frame(input_path)
            image_sequences = []
            for root, directories, filenames in os.walk(input_path):
                # If there is more than 1 image file in input_path, search this path for file sequences also
                if root == input_path:
                    image_files = [
                        f
                        for f in filenames
                        if os.path.splitext(f)[-1][1:] in input_image_formats
                    ]
                    if len(image_files) > 1:
                        image_sequences += pyseq.get_sequences(input_path)
                for directory in directories:
                    image_sequences += pyseq.get_sequences(
                        os.path.join(root, directory)
                    )
            if not image_sequences:
                log.error(
                    "Could not find any image files recursively in source directory: {0}".format(
                        input_path
                    )
                )
                return None
        elif os.path.isfile(input_path):
            # Assume it's the first frame of the image sequence
            # Try to split off the frame number to get a glob
            image = pyseq.get_sequences(input_path)
            if image:
                image = image[0]
            image_sequences = pyseq.get_sequences(
                os.path.join(image.dirname, image.name.split(image.parts[-2])[0]) + "*"
            )

        else:
            # Assume this is a %05d or ### image sequence. Use the parent directory if it exists.
            dirname, filename = os.path.split(input_path)
            if os.path.isdir(dirname):
                image_sequences = pyseq.get_sequences(dirname)
            else:
                image_sequences = None

        if image_sequences:
            # Remove image sequences not in list of approved extensions
            if not input_image_formats:
                input_image_formats = ["exr"]
            actual_image_sequences = []
            for image_sequence in image_sequences:
                extension = image_sequence.name.split(".")[-1]
                if extension in input_image_formats:
                    actual_image_sequences.append(image_sequence)
            print("Found image sequences: \n{0}".format(actual_image_sequences))
            return actual_image_sequences
        else:
            log.error("Could not find any Image Sequences!!!")
            return None

    def create_temp_frame(self, input_path):
        import random

        output_width = self.globals_config["width"]
        output_height = self.globals_config["height"]

        image_buf = oiio.ImageBufAlgo.zero(
            oiio.ROI(0, output_width, 0, output_height, 0, 1, 0, 3)
        )

        f = os.listdir(input_path)

        folder_path, base_filename = os.path.split(f[1])
        base_filename_without_ext, ext = os.path.splitext(base_filename)
        zero_frame_filename = f"{base_filename_without_ext[:-4]}0000{ext}"
        first_frame_filename = f"{base_filename_without_ext[:-4]}0001{ext}"
        self.first_frame_path = os.path.join(input_path, first_frame_filename)
        self.renamed_file = os.path.join(input_path, zero_frame_filename)
        image_buf.write(self.renamed_file)


def main():
    daily = GenrateDaily(
        scope="Asset/env/CHAD_LAB_BUNKER_EXTERIOR", project="bn2", task_id=125880
    )


if __name__ == "__main__":
    main()
