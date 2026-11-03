public class StringConstructors05 {
  public static boolean f(String arg) {
    char[] charArray = {'d', 'i', 'f', 'f', 'b', 'l', 'u', 'e'};
    String s3 = new String(charArray);
    return s3.equals(arg);
  }
}
