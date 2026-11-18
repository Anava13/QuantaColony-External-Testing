// The study's Python engine in the browser: Pyodide, the scientific libraries, and QuantaColony's own
// code (python.zip: core, data and study). Everything Colony Detection decides runs in Python
// (quantacolony/study/web.py); this file only loads it and converts values between the two languages.

const CONFIG = window.STUDY_CONFIG || {};
const PYODIDE = CONFIG.pyodideURL || "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";
const PACKAGES = ["numpy", "scipy", "scikit-image", "pillow", "sqlite3"];

function loadScript(url) {
  return new Promise((resolve, reject) => {
    const tag = document.createElement("script");
    tag.src = url;
    tag.onload = resolve;
    tag.onerror = () => reject(new Error(`Could not load ${url}`));
    document.head.appendChild(tag);
  });
}

export class Engine {
  // progress(fraction, text) reports the loading steps
  async load(progress) {
    progress(0.05, "Loading Python (first visit: about a minute)…");
    await loadScript(PYODIDE + "pyodide.js");
    this.py = await window.loadPyodide({ indexURL: PYODIDE });
    progress(0.35, "Loading the image libraries…");
    await this.py.loadPackage(PACKAGES, { messageCallback: () => {} });
    progress(0.8, "Loading QuantaColony…");
    const archive = await (await fetch(CONFIG.pythonURL || "python.zip", { cache: "no-cache" })).arrayBuffer();
    this.py.unpackArchive(archive, "zip", { extractDir: "/home/pyodide/app" });
    await this.py.runPythonAsync(`
import sys
sys.path.insert(0, "/home/pyodide/app")
from quantacolony.study.web import WebStudy
study = WebStudy("/home/pyodide/app/quantacolony/study/bundle", "/home/pyodide/work")
`);
    this.study = this.py.globals.get("study");
    progress(1, "Ready");
  }

  // Call a WebStudy method; Python values come back as plain JavaScript values.
  call(name, ...args) {
    const pyArgs = args.map(a => (a !== null && typeof a === "object") ? this.py.toPy(a) : a);
    const result = this.study[name](...pyArgs);
    pyArgs.forEach(a => a && a.destroy && a.destroy());
    if (result && typeof result.toJs === "function") {
      const value = result.toJs({ dict_converter: Object.fromEntries, create_pyproxies: false });
      result.destroy();
      return value;
    }
    return result;
  }

  // The plate's crop as ImageData
  image(width, height) {
    const bytes = this.study.image_rgba();
    const data = bytes.toJs();
    bytes.destroy();
    return new ImageData(new Uint8ClampedArray(data.buffer, data.byteOffset, data.byteLength), width, height);
  }
}
