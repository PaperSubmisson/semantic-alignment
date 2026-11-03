public class StaticCharMethods06 {
  public static int main(String arg) {
    if (arg.length() < 1) return -1;

    char c = arg.charAt(0);
    Character c1 = c;
    Character c2 = c;

    if (c1.equals(c2)) {
      return 1;
    } else {
      return 0;
    }
  }
}
