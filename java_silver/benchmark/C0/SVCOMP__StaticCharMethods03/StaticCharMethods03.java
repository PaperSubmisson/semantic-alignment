public class StaticCharMethods03 {
  public static int f(String arg) {
    if (arg.length() < 1) return -1;

    char c = arg.charAt(0);
    return (Character.isDefined(c) == false) ? 1 : 0;
  }
}
