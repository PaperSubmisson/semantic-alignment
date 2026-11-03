public class StringConstructors03 {
  public static boolean f(String arg0, String arg1) {
    String s = new String(arg0);
    String s2 = new String(s);
    return s2.equals(arg1);
  }
}
