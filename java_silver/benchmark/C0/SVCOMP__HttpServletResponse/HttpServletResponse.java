import java.io.IOException;
import java.io.PrintWriter;

public class HttpServletResponse {

  public void sendRedirect(String string) throws IOException {
    checkNoSymbolic(string);
  }

  public PrintWriter getWriter() throws IOException {
    return new PrintWriter(System.out) {
      @Override
      public void println(String x) {
        checkNoSymbolic(x);
      }

      @Override
      public void println(Object x) {
        checkNoSymbolic(String.valueOf(x));
      }
    };
  }

  public void setContentType(String s) {
    checkNoSymbolic(s);
  }

  private void checkNoSymbolic(String s) {
    if (s != null && s.contains("<bad/>")) {
      assert s.indexOf("<bad/>") >= 0;
    }
  }
}
