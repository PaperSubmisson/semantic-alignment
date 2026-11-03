public class StringBuilderCapLen03 {
  public static boolean f(String arg) {
    StringBuilder buffer = new StringBuilder(arg);
    return buffer.length() == 51;
  }
}
