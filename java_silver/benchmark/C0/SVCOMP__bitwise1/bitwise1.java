public class bitwise1 {
  static char c;

  public static boolean fun(int input) {
    c = (char) (input * 2 + 1);
    int i = (c | 2);
    return (i & 3) == 3;
  }
}
