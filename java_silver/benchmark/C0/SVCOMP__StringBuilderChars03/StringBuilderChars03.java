public class StringBuilderChars03 {
  public static boolean f(String arg) {
    StringBuilder buffer = new StringBuilder(arg);
    return buffer.charAt(0) == buffer.charAt(4);
  }
}
