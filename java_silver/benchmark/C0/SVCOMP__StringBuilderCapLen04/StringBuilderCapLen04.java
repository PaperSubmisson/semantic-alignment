public class StringBuilderCapLen04 {
  public static boolean f(String arg) {
    StringBuilder buffer = new StringBuilder(arg);
    return buffer.capacity() == 69;
  }
}
