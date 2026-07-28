"""针对 .NET CLR 与 Windows Forms 异常捕获的防护补丁。"""

def setup_clr_exception_hook():
    """
    注册 .NET/WinForms 异常捕获，防范 COM/WebView2 崩溃。
    """
    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        from System.Windows.Forms import Application, UnhandledExceptionMode
        from System import AppDomain

        def on_thread_exception(sender, args):
            try:
                ex = args.Exception
                msg = f"[CLR ThreadException] 捕获 .NET UI 线程未处理异常: {ex.Message}\n堆栈: {ex.StackTrace}"
                print(msg)
                from main import _write_crash_log
                _write_crash_log(msg)
            except Exception:
                pass

        def on_unhandled_exception(sender, args):
            try:
                ex = args.ExceptionObject
                is_terminating = args.IsTerminating
                msg = f"[CLR AppDomainException] 捕获 .NET 全局未处理异常: {ex}\n是否正在终止: {is_terminating}"
                print(msg)
                from main import _write_crash_log
                _write_crash_log(msg)
            except Exception:
                pass

        Application.ThreadException += on_thread_exception
        Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException)
        AppDomain.CurrentDomain.UnhandledException += on_unhandled_exception
        print("[CLR防护] .NET Windows Forms 异常钩子注册成功")
    except Exception as e:
        print(f"[CLR防护] .NET 异常钩子注册失败: {e}")
