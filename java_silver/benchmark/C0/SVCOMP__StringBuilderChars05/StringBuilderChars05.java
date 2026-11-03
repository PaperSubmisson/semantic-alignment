public class StringBuilderChars05 {
  public static boolean f(String arg) {
    StringBuilder buffer = new StringBuilder(arg);
    buffer.setCharAt(0, 'H');
    buffer.setCharAt(6, 'T');
    return buffer.toString().equals("HiffBllTe Limited");
  }
}
