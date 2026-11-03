public class StringValueOf09 {
  public static boolean f(String arg) {
    double doubleValue = Double.parseDouble(arg); // no suffix, double is default
    String tmp = String.valueOf(doubleValue);
    return tmp.equals("33.3333");
  }
}
