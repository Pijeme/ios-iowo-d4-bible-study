#import "PythonHost.h"
#include <Python/Python.h>

static IOWONativeDispatch nativeDispatch;
static BOOL started = NO;

static PyObject *nativeCall(PyObject *self, PyObject *args) {
    const char *method, *payload;
    if (!PyArg_ParseTuple(args, "ss", &method, &payload)) return NULL;
    NSString *name = [NSString stringWithUTF8String:method];
    NSString *body = [NSString stringWithUTF8String:payload];
    __block NSString *reply;
    // Python request threads may call Keychain, file IO or main-thread PDF UI.
    // Release the GIL while the native operation is in progress.
    Py_BEGIN_ALLOW_THREADS
    @autoreleasepool { reply = nativeDispatch(name, body); }
    Py_END_ALLOW_THREADS
    return PyUnicode_FromString(reply.UTF8String);
}

static PyMethodDef nativeMethods[] = {
    {"call", nativeCall, METH_VARARGS, "Call an allow-listed iOS service."},
    {NULL, NULL, 0, NULL}
};
static struct PyModuleDef nativeModule = {PyModuleDef_HEAD_INIT, "_iowo_native", NULL, -1, nativeMethods};
PyMODINIT_FUNC PyInit__iowo_native(void) { return PyModule_Create(&nativeModule); }

static void pythonError(NSError **error) {
    PyObject *type = NULL, *value = NULL, *trace = NULL;
    PyErr_Fetch(&type, &value, &trace);
    PyObject *description = value ? PyObject_Str(value) : NULL;
    NSString *message = description ? [NSString stringWithUTF8String:PyUnicode_AsUTF8(description)] : @"The study engine could not start.";
    if (error) *error = [NSError errorWithDomain:@"IOWOStudyEngine" code:1
                                      userInfo:@{NSLocalizedDescriptionKey:message ?: @"Study engine error"}];
    Py_XDECREF(description); Py_XDECREF(type); Py_XDECREF(value); Py_XDECREF(trace);
}

NSString *IOWOStartEngine(NSString *resourcePath, NSString *dataPath,
                          IOWONativeDispatch handler, NSError **error) {
    @synchronized([NSBundle class]) {
        if (started) {
            if (error) *error = [NSError errorWithDomain:@"IOWOStudyEngine" code:2
                                               userInfo:@{NSLocalizedDescriptionKey:@"The engine is already running."}];
            return nil;
        }
        nativeDispatch = [handler copy];
        PyImport_AppendInittab("_iowo_native", PyInit__iowo_native);
        NSString *home = [resourcePath stringByAppendingPathComponent:@"python"];
        PyConfig config; PyConfig_InitIsolatedConfig(&config);
        config.write_bytecode = 0;
        config.install_signal_handlers = 0;
        PyStatus status = PyConfig_SetBytesString(&config, &config.home, home.UTF8String);
        if (!PyStatus_Exception(status)) status = Py_InitializeFromConfig(&config);
        PyConfig_Clear(&config);
        if (PyStatus_Exception(status)) {
            if (error) *error = [NSError errorWithDomain:@"IOWOStudyEngine" code:3
                                               userInfo:@{NSLocalizedDescriptionKey:@"The bundled Python runtime could not initialize."}];
            return nil;
        }
        // All application code and packages are bundled; never download code.
        PyObject *path = PySys_GetObject("path");
        for (NSString *relative in @[@"StudyEngine/site-packages", @"StudyEngine/app"]) {
            NSString *location = [resourcePath stringByAppendingPathComponent:relative];
            PyObject *entry = PyUnicode_FromString(location.UTF8String);
            PyList_Insert(path, 0, entry); Py_DECREF(entry);
        }
        PyObject *module = PyImport_ImportModule("ios_bridge");
        PyObject *result = module ? PyObject_CallMethod(module, "start", "ss", dataPath.UTF8String,
                                   [[resourcePath stringByAppendingPathComponent:@"Resources"] UTF8String]) : NULL;
        NSString *endpoint = result ? [NSString stringWithUTF8String:PyUnicode_AsUTF8(result)] : nil;
        if (!result) pythonError(error);
        Py_XDECREF(result); Py_XDECREF(module);
        started = endpoint != nil;
        PyEval_SaveThread();
        return endpoint;
    }
}

NSString *IOWOEngineSyncStatus(NSError **error) {
    if (!started) return @"{\"running\":false}";
    PyGILState_STATE gil = PyGILState_Ensure();
    PyObject *module = PyImport_ImportModule("ios_bridge");
    PyObject *result = module ? PyObject_CallMethod(module, "sync_status", NULL) : NULL;
    NSString *reply = result ? [NSString stringWithUTF8String:PyUnicode_AsUTF8(result)] : nil;
    if (!result) pythonError(error);
    Py_XDECREF(result); Py_XDECREF(module); PyGILState_Release(gil);
    return reply;
}
