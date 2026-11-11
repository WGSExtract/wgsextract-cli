import assert from "node:assert/strict";
import test from "node:test";

const { leaveAppImageEnvironment } = await import("../dist/web/src/server/appimage-environment.js");

const appDir = "/tmp/appimage_extracted_91458b0c";

test("AppImage variables pointing into the image are dropped or trimmed", () => {
  const environment = {
    APPDIR: appDir,
    APPIMAGE: "/home/user/Applications/WGSExtract.AppImage",
    LD_LIBRARY_PATH: `${appDir}/usr/lib/:${appDir}/lib/x86_64-linux-gnu/:`,
    PATH: `${appDir}/usr/bin/:${appDir}/bin/:/usr/local/bin:/usr/bin:/bin`,
    XDG_DATA_DIRS: `${appDir}/usr/share/:${appDir}/usr/share:/usr/share:`,
    PYTHONHOME: `${appDir}/usr/`,
    GDK_PIXBUF_MODULE_FILE: `${appDir}//usr/lib/gdk-pixbuf-2.0/2.10.0/loaders.cache`,
    GSETTINGS_SCHEMA_DIR: `${appDir}/usr/share/glib-2.0/schemas/:${appDir}//usr/share/glib-2.0/schemas`,
    HOME: "/home/user",
    GTK_THEME: "Adwaita:light",
    WGSEXTRACT_GUI_SETUP_EVENTS_LOG: "/tmp/events.ndjson",
  };
  leaveAppImageEnvironment(environment);
  assert.deepEqual(environment, {
    APPIMAGE: "/home/user/Applications/WGSExtract.AppImage",
    PATH: "/usr/local/bin:/usr/bin:/bin",
    XDG_DATA_DIRS: "/usr/share",
    HOME: "/home/user",
    GTK_THEME: "Adwaita:light",
    WGSEXTRACT_GUI_SETUP_EVENTS_LOG: "/tmp/events.ndjson",
  });
});

test("a folder that only starts like the image's is kept", () => {
  const environment = {
    APPDIR: appDir,
    APPIMAGE: "/home/user/WGSExtract.AppImage",
    PATH: `${appDir}/usr/bin:${appDir}-other/bin:/usr/bin`,
  };
  leaveAppImageEnvironment(environment);
  assert.equal(environment.PATH, `${appDir}-other/bin:/usr/bin`);
});

test("an extracted AppImage, which sets no APPIMAGE, is cleaned too", () => {
  const environment = { APPDIR: appDir, LD_LIBRARY_PATH: `${appDir}/usr/lib:`, PATH: `${appDir}/usr/bin:/usr/bin` };
  leaveAppImageEnvironment(environment);
  assert.deepEqual(environment, { PATH: "/usr/bin" });
});

test("outside an AppImage the environment is left alone", () => {
  const environment = { LD_LIBRARY_PATH: "/opt/lib:", PATH: "/usr/bin:" };
  leaveAppImageEnvironment(environment);
  assert.deepEqual(environment, { LD_LIBRARY_PATH: "/opt/lib:", PATH: "/usr/bin:" });
});
