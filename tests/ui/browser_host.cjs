/* Stop only the synthetic host spawned by this module, including Windows venv launchers. */
const {spawn, execFile} = require('node:child_process');
const owned = new WeakSet();
const stopping = new WeakMap();

function spawnHostProcess(command, args, options) {
  const child = spawn(command, args, {...options, windowsHide: true});
  owned.add(child);
  return child;
}

function stopHostProcess(child) {
  if (!owned.has(child)) return Promise.reject(new Error('Refusing to stop an unowned browser host'));
  if (stopping.has(child)) return stopping.get(child);
  const stop = (async () => {
    if (!Number.isSafeInteger(child.pid) || child.pid <= 0 || child.exitCode !== null || child.signalCode !== null) return;
    if (process.platform === 'win32') {
      // child is the exact live ChildProcess returned by our spawn, never a searched PID.
      await new Promise((resolve, reject) => {
        execFile('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], {windowsHide: true}, error => {
          if (!error || child.exitCode !== null || child.signalCode !== null) resolve();
          else reject(error);
        });
      });
    } else {
      await new Promise(resolve => {
        child.once('exit', resolve);
        if (!child.kill()) { child.removeListener('exit', resolve); resolve(); }
      });
    }
  })();
  stopping.set(child, stop);
  return stop;
}

module.exports = {spawnHostProcess, stopHostProcess};
