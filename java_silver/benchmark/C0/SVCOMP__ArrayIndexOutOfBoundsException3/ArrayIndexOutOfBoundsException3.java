public class ArrayIndexOutOfBoundsException3 {
  public static boolean func(int index) {
    try {
      int[] a = new int[4];
      a[index] = 0;
    } catch (Exception exc) {
      return false;
    }
    return true;
  }
}
