#*****************************************************************************
#
# PlatformHTTPWrapper.py
#     Singleton process wrapping SIO and providing the ability to classify
#     the moving objects we track.
#
#
#*****************************************************************************
#
#
# Copyright 2013-2022 Sighthound, Inc.
#
# Licensed under the GNU GPLv3 license found at
# https://www.gnu.org/licenses/gpl-3.0.txt
#
# Alternative licensing available from Sighthound, Inc.
# by emailing opensource@sighthound.com
#
# This file is part of the Sighthound Video project which can be found at
# https://github.com/sighthoundinc/SighthoundVideo
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; using version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02111, USA.
#
#
#*****************************************************************************

import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from . import MessageIds
import traceback
import time
import threading

from backEnd.native.detector import (
    encode_detection_response,
    parse_rgb_body,
)
from backEnd.native.onnx_detector import create_detector


#from DebugLogManager import DebugLogManager

from vitaToolbox.loggingUtils.LoggingUtils import getLogger
from appCommon.resources import model_dir

################################################################################
# TODO: duplicated into ObjectDetectorClient.py, too small for its own module ... resolve the redundancy
def _getModelDir():
    return model_dir()

################################################################################
def _as_body(text):
    """HTTP bodies are bytes. Accept a str for the error text we already build."""
    if isinstance(text, bytes):
        return text
    return str(text).encode("utf-8")


def HandlerFactory(logger, detector):
    class PlatformHTTPWrapperHandler(BaseHTTPRequestHandler, object):
        #---------------------------------------------------------------------------
        def __init__(self, *args, **kwargs):
            self._logger = logger
            self._detector = detector
            super(PlatformHTTPWrapperHandler, self).__init__(*args, **kwargs)

        #---------------------------------------------------------------------------
        def _sendResponse(self, response, text, contentType):
            body = _as_body(text)
            self.send_response(response)
            self.send_header("Content-type", contentType)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        #---------------------------------------------------------------------------
        def _sendJSONResponse(self, payload):
            self._sendResponse(200, payload, "application/json")

        #---------------------------------------------------------------------------
        def _sendErrorResponse(self, error):
            self._sendResponse(500, error + "\n", "text/plain")

        #---------------------------------------------------------------------------
        def do_GET(self):
            self._sendErrorResponse("GET is not supported")

        #---------------------------------------------------------------------------
        def do_HEAD(self):
            self._sendErrorResponse("HEAD is not supported")

        #---------------------------------------------------------------------------
        def do_POST(self):
            """Raw RGB in, JSON detections out.

            Headers: Content-Length, X-Width, X-Height. The body is
            ``width * height * 3`` bytes of RGB. The JSON objects may include
            ``attributes`` (plate text, face identity, delivery carrier) in
            addition to type, boundingBox and score.
            """
            self._logger.debug("Processing POST - %d!" % threading.current_thread().ident)
            kMaxPostLen = 25 * 1024 * 1024  # 25MB
            start = time.time()
            try:
                contentLength = int(self.headers.get("Content-Length") or 0)
                width = int(self.headers.get("X-Width") or 0)
                height = int(self.headers.get("X-Height") or 0)
                if contentLength <= 0 or contentLength > kMaxPostLen:
                    self._sendErrorResponse("Invalid content length: " + str(contentLength))
                    return
                if width <= 0 or height <= 0:
                    self._sendErrorResponse("Invalid dims: x=" + str(width) + " y=" + str(height))
                    return

                image = self.rfile.read(contentLength)
                frame = parse_rgb_body(image, width, height)
                detections = self._detector.detect_rgb(frame, width, height)
                output = encode_detection_response(detections)
                duration = int((time.time() - start) * 1000)
                self._logger.debug(
                    "Detection done in %dms for %dx%d (%d bytes, %d objects)"
                    % (duration, width, height, len(frame), len(detections))
                )
            except Exception:
                self._logger.error("Error while processing POST request: " + traceback.format_exc())
                self._sendErrorResponse("Failed to parse POST parameters")
                return

            self._sendJSONResponse(output)

    return PlatformHTTPWrapperHandler


################################################################################
class PlatformHTTPWrapper(object):
    _kLogName = "PlatformHTTPWrapper.log"

    #---------------------------------------------------------------------------
    def __init__(self, msgQ, serverAddress, localDataDir):
        """Initialize PlatformHTTPWrapper.

        @param  port             The port to run on
        @param  localDataDir     The directory in which to store application data.
        """
        # Call the superclass constructor.
        super(PlatformHTTPWrapper, self).__init__()

        # Save data dir and setup logging...  SHOULD BE FIRST!
        self._localDataDir = localDataDir
        self._logDir = os.path.join(self._localDataDir, "logs")
        self._logger = getLogger(PlatformHTTPWrapper._kLogName, self._logDir)
        self._logger.grabStdStreams()
        self._msgQ = msgQ

        enableGpu = True  # DirectML or CUDA when that provider is installed; CPU otherwise.
        self._detector = create_detector(self._logger, _getModelDir(), enable_gpu=enableGpu)

        HandlerClass = HandlerFactory(self._logger, self._detector)
        HTTPServer.allow_reuse_address = True
        self._httpServer = HTTPServer(serverAddress, HandlerClass)
        port = self._httpServer.server_address[1]
        if not self._msgQ is None:
            # The opposite should only happen in unit test context
            self._msgQ.put([MessageIds.msgIdAnalyticsPortChanged, port])

        #self._debugLogManager = DebugLogManager("PlatformHTTPWrapper", localDataDir)
        self._logger.info("PlatformHTTPWrapper running on port %i, pid: %d, tid %d" %
                          (port, os.getpid(), threading.current_thread().ident))

    #---------------------------------------------------------------------------
    def getPort(self):
        return self._httpServer.server_address[1]

    #---------------------------------------------------------------------------
    def run(self):
        self._httpServer.serve_forever()

    #---------------------------------------------------------------------------
    def getLogger(self):
        return self._logger


################################################################################
def runPlatformHTTPWrapper(msgQ, localDataDir, port):
    # should only be accessible via localhost
    serverAddress = ('127.0.0.1', port)
    httpd = PlatformHTTPWrapper(msgQ, serverAddress, localDataDir)
    httpd.run()

################################################################################
if __name__ == "__main__":
    from sys import argv
    runPlatformHTTPWrapper("/tmp", 7050)
