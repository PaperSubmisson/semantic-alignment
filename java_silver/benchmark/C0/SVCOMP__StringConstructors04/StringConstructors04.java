public class StringConstructors04 {
  public static boolean f(String arg) {
    char[] charArray = {'d', 'i', 'f', 'f', 'b', 'l', 'u', 'e'};
    String s4 = new String(charArray, 4, 4);
    return s4.equals(arg);
  }
}
