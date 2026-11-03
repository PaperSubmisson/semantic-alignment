public class StaticCharMethods02 {
  public static int f(String arg) {
    if (arg.length() < 1) return -1;
    char c = arg.charAt(0);
    return (Character.toUpperCase(c) != Character.toLowerCase(c)) ? 1 : 0;
  }
}
