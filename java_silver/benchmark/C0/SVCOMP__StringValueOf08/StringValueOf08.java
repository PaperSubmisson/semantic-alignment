public class StringValueOf08 {
  public static boolean f(String arg) {
    float floatValue = Float.parseFloat(arg);
    String tmp = String.valueOf(floatValue);
    return tmp.equals("2.50");
  }
}
