/**
 * Note: When using the Node.JS APIs, the config file
 * doesn't apply. Instead, pass options directly to the APIs.
 *
 * All configuration options: https://remotion.dev/docs/config
 */

import { Config } from "@remotion/cli/config";

Config.setRspack(true);
Config.setVideoImageFormat("jpeg");
Config.setOverwriteOutput(true);
Config.setDefaultPremountInSeconds(2);

// Local fix (macOS 26 + Remotion's Chromium 157 headless-shell build): its GPU helper aborts with
// "icudtl.dat not found in bundle". Chrome for Testing renders fine.
Config.setChromeMode("chrome-for-testing");
// Keep the intermediate frames near-lossless; final encode is h264 CRF 16.
Config.setJpegQuality(95);
