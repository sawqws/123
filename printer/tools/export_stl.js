// Runs the viewer's geometry code in Node and writes the printable parts as binary STL.
// Usage: node export_stl.js <three.min.js> <organizers.html> <output dir>
const fs = require("fs"), path = require("path"), vm = require("vm");
const [threePath, htmlPath, outDir] = process.argv.slice(2);

const THREE = {};
vm.runInNewContext(fs.readFileSync(threePath, "utf8"), { exports: THREE, module: {}, console, self: {} });
THREE.OrbitControls = function () { return { update() {}, addEventListener() {}, target: new THREE.Vector3() }; };
THREE.WebGLRenderer = function () { return { domElement: {}, setPixelRatio() {}, setSize() {}, render() {} }; };

const element = () => ({
  value: "", checked: true, textContent: "", innerHTML: "", dataset: {}, firstChild: null, clientWidth: 800, clientHeight: 600,
  remove() {}, addEventListener() {}, setAttribute() {}, insertBefore() {}, closest() { return null; }
});
const document = {
  getElementById: element, querySelectorAll: () => [],
  createElement: () => ({ getContext: () => new Proxy({}, { get: () => () => {}, set: () => true }), width: 0, height: 0 })
};
const window = { THREE, devicePixelRatio: 1 };
const sandbox = { window, document, THREE, console, ResizeObserver: function () { return { observe() {} }; }, requestAnimationFrame() { return 1; } };

const html = fs.readFileSync(htmlPath, "utf8");
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
vm.runInNewContext(scripts[scripts.length - 1][1], sandbox);

fs.mkdirSync(outDir, { recursive: true });
const parts = window.__organizerParts();
for (const [name, buf] of Object.entries(parts)) {
  const b = Buffer.from(buf);
  b.write("organizer part".padEnd(80), 0, "ascii");
  fs.writeFileSync(path.join(outDir, name), b);
  console.log(name, b.length, "bytes,", b.readUInt32LE(80), "triangles");
}
