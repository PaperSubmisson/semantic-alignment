public class StringValueOf05 {
  public static boolean f(String arg) {
    if (arg.length() < 1) return false;

    char characterValue = arg.charAt(0);
    String tmp = String.valueOf(characterValue);
    return tmp.equals("A");
  }
}
